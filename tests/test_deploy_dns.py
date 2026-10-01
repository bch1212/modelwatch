import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts.deploy_dns import (
    CloudflareAPIError,
    CloudflareClient,
    RailwayAPIError,
    RailwayStateError,
    ZoneNotFound,
    get_railway_domain_records,
    main,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class RecordingOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=30):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def request_body(request):
    return json.loads(request.data.decode()) if request.data else None


def test_zone_lookup_rejects_cloudflare_success_false():
    opener = RecordingOpener(
        FakeResponse(
            {
                "success": False,
                "errors": [{"code": 9109, "message": "Invalid access token"}],
                "result": [],
            }
        )
    )
    client = CloudflareClient("test-token", opener=opener)

    with pytest.raises(CloudflareAPIError, match="Invalid access token"):
        client.get_zone_id("modelwatch.app")


def test_zone_lookup_rejects_http_auth_error():
    opener = RecordingOpener(
        HTTPError(
            "https://api.cloudflare.com/client/v4/zones",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"success":false,"errors":[{"message":"forbidden"}]}'),
        )
    )
    client = CloudflareClient("test-token", opener=opener)

    with pytest.raises(CloudflareAPIError, match="HTTP 403"):
        client.get_zone_id("modelwatch.app")


def test_zone_lookup_distinguishes_missing_zone_from_api_failure():
    opener = RecordingOpener(FakeResponse({"success": True, "errors": [], "result": []}))
    client = CloudflareClient("test-token", opener=opener)

    with pytest.raises(ZoneNotFound, match="modelwatch.app"):
        client.get_zone_id("modelwatch.app")


def test_txt_collision_creates_additional_record_without_overwriting_google_verification():
    opener = RecordingOpener(
        FakeResponse(
            {
                "success": True,
                "errors": [],
                "result": [
                    {
                        "id": "google-record",
                        "type": "TXT",
                        "name": "_railway-verify.api.modelwatch.app",
                        "content": "google-site-verification=abc123",
                        "proxied": False,
                    }
                ],
            }
        ),
        FakeResponse(
            {
                "success": True,
                "errors": [],
                "result": {"id": "railway-record"},
            }
        ),
    )
    client = CloudflareClient("test-token", opener=opener)

    result = client.upsert_record(
        "zone-id",
        "TXT",
        "_railway-verify.api.modelwatch.app",
        "railway-verification=expected",
        proxied=False,
        match_content=True,
    )

    assert result == "created"
    assert len(opener.requests) == 2
    create_request = opener.requests[1][0]
    assert create_request.get_method() == "POST"
    assert create_request.full_url.endswith("/zones/zone-id/dns_records")
    assert request_body(create_request) == {
        "type": "TXT",
        "name": "_railway-verify.api.modelwatch.app",
        "content": "railway-verification=expected",
        "proxied": False,
    }
    assert all("google-record" not in request.full_url for request, _ in opener.requests)


def test_txt_exact_value_is_reused_without_touching_other_txt_records():
    opener = RecordingOpener(
        FakeResponse(
            {
                "success": True,
                "errors": [],
                "result": [
                    {
                        "id": "google-record",
                        "type": "TXT",
                        "name": "_railway-verify.api.modelwatch.app",
                        "content": "google-site-verification=abc123",
                        "proxied": False,
                    },
                    {
                        "id": "railway-record",
                        "type": "TXT",
                        "name": "_railway-verify.api.modelwatch.app",
                        "content": "railway-verification=expected",
                        "proxied": False,
                    },
                ],
            }
        )
    )
    client = CloudflareClient("test-token", opener=opener)

    result = client.upsert_record(
        "zone-id",
        "TXT",
        "_railway-verify.api.modelwatch.app",
        "railway-verification=expected",
        proxied=False,
        match_content=True,
    )

    assert result == "unchanged"
    assert len(opener.requests) == 1


def test_dns_write_rejects_cloudflare_api_level_failure():
    opener = RecordingOpener(
        FakeResponse({"success": True, "errors": [], "result": []}),
        FakeResponse(
            {
                "success": False,
                "errors": [{"code": 1004, "message": "DNS validation failed"}],
                "result": None,
            }
        ),
    )
    client = CloudflareClient("test-token", opener=opener)

    with pytest.raises(CloudflareAPIError, match="DNS validation failed"):
        client.upsert_record(
            "zone-id",
            "CNAME",
            "api.modelwatch.app",
            "exact-target.up.railway.app",
            proxied=False,
        )


def multi_environment_status():
    return {
        "id": "project-id",
        "environments": {
            "edges": [
                {
                    "node": {
                        "id": "preview-env",
                        "name": "preview",
                        "serviceInstances": {
                            "edges": [
                                {
                                    "node": {
                                        "environmentId": "preview-env",
                                        "serviceId": "preview-backend",
                                        "serviceName": "backend",
                                    }
                                }
                            ]
                        },
                    }
                },
                {
                    "node": {
                        "id": "production-env",
                        "name": "production",
                        "serviceInstances": {
                            "edges": [
                                {
                                    "node": {
                                        "environmentId": "production-env",
                                        "serviceId": "production-backend",
                                        "serviceName": "backend",
                                    }
                                }
                            ]
                        },
                    }
                },
            ]
        },
    }


def test_railway_domain_query_uses_backend_from_explicit_production_environment():
    opener = RecordingOpener(
        FakeResponse(
            {
                "data": {
                    "domains": {
                        "customDomains": [
                            {
                                "domain": "api.modelwatch.app",
                                "status": {
                                    "dnsRecords": [
                                        {
                                            "recordType": "DNS_RECORD_TYPE_CNAME",
                                            "requiredValue": "exact-production-target.up.railway.app",
                                            "purpose": "ROUTING",
                                        }
                                    ],
                                    "verificationDnsHost": "_railway-verify.api.modelwatch.app",
                                    "verificationToken": "railway-verification=expected",
                                },
                            }
                        ]
                    }
                }
            }
        )
    )

    records = get_railway_domain_records(
        multi_environment_status(),
        environment_name="production",
        service_name="backend",
        domain="api.modelwatch.app",
        api_token="railway-token",
        opener=opener,
    )

    assert records.cname == "exact-production-target.up.railway.app"
    assert records.verification_host == "_railway-verify.api.modelwatch.app"
    assert records.verification_token == "railway-verification=expected"
    graphql_request = opener.requests[0][0]
    assert request_body(graphql_request)["variables"] == {
        "environmentId": "production-env",
        "projectId": "project-id",
        "serviceId": "production-backend",
    }


def test_railway_selection_rejects_missing_explicit_environment():
    with pytest.raises(RailwayStateError, match="staging"):
        get_railway_domain_records(
            multi_environment_status(),
            environment_name="staging",
            service_name="backend",
            domain="api.modelwatch.app",
            api_token="railway-token",
            opener=RecordingOpener(),
        )


def test_railway_graphql_errors_fail_closed():
    opener = RecordingOpener(
        FakeResponse(
            {
                "data": None,
                "errors": [{"message": "not authorized for environment"}],
            }
        )
    )

    with pytest.raises(RailwayAPIError, match="not authorized"):
        get_railway_domain_records(
            multi_environment_status(),
            environment_name="production",
            service_name="backend",
            domain="api.modelwatch.app",
            api_token="railway-token",
            opener=opener,
        )


def test_deploy_script_runs_fail_closed_dns_helper_before_done_banner():
    script = (ROOT / "deploy.sh").read_text()
    helper_call = 'python3 "$REPO_ROOT/scripts/deploy_dns.py" cloudflare'

    assert helper_call in script
    assert script.index(helper_call) < script.index('echo "=== DONE ==="')
    assert f"{helper_call} ||" not in script
    assert "| python3" not in script[script.index(helper_call) : script.index('echo "=== DONE ==="')]


def test_cloudflare_cli_api_failure_is_nonzero_and_never_prints_done(
    monkeypatch, capsys
):
    opener = RecordingOpener(
        FakeResponse(
            {
                "success": False,
                "errors": [{"message": "token rejected"}],
                "result": [],
            }
        )
    )
    monkeypatch.setattr("scripts.deploy_dns._URL_OPEN", opener)

    result = main(
        [
            "cloudflare",
            "--domain",
            "modelwatch.app",
            "--cname",
            "exact-target.up.railway.app",
            "--token",
            "test-token",
        ]
    )
    output = capsys.readouterr()

    assert result != 0
    assert "token rejected" in output.err
    assert "DONE" not in output.out
