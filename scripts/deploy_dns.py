#!/usr/bin/env python3
"""Fail-closed Railway domain lookup and Cloudflare DNS provisioning."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError


_URL_OPEN = urllib.request.urlopen
DEFAULT_CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"


class DeployDNSError(RuntimeError):
    """Base exception for safe deploy/DNS failures."""


class CloudflareAPIError(DeployDNSError):
    """Cloudflare transport, HTTP, API envelope, or response error."""


class ZoneNotFound(DeployDNSError):
    """The Cloudflare request succeeded, but the requested zone is absent."""


class RailwayAPIError(DeployDNSError):
    """Railway transport, HTTP, GraphQL, or response error."""


class RailwayStateError(DeployDNSError):
    """The Railway CLI state cannot identify the requested environment/service."""


class RailwayDomainNotFound(DeployDNSError):
    """The requested Railway custom domain has not been attached yet."""


@dataclass(frozen=True)
class RailwayDomainRecords:
    cname: str
    verification_host: str = ""
    verification_token: str = ""


def _error_messages(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return "invalid error response"
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return "unspecified API error"
    messages = []
    for error in errors:
        if isinstance(error, Mapping):
            message = error.get("message")
            code = error.get("code")
            if message:
                messages.append(f"{code}: {message}" if code is not None else str(message))
    return "; ".join(messages) or "unspecified API error"


def _decode_json(raw: bytes, provider: str, error_type: type[DeployDNSError]) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error_type(f"{provider} returned invalid JSON") from exc


def _http_error_detail(exc: HTTPError, provider: str) -> str:
    try:
        payload = _decode_json(exc.read(), provider, DeployDNSError)
    except DeployDNSError:
        return exc.reason or "request failed"
    return _error_messages(payload)


class CloudflareClient:
    def __init__(
        self,
        token: str,
        *,
        opener: Callable[..., Any] | None = None,
        base_url: str = DEFAULT_CLOUDFLARE_API_BASE,
    ) -> None:
        if not token:
            raise CloudflareAPIError("Cloudflare token is missing")
        self.token = token
        self.opener = opener or _URL_OPEN
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "modelwatch-deploy",
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                status = getattr(response, "status", 200)
                raw = response.read()
        except HTTPError as exc:
            detail = _http_error_detail(exc, "Cloudflare")
            raise CloudflareAPIError(f"Cloudflare HTTP {exc.code}: {detail}") from exc
        except (URLError, OSError) as exc:
            raise CloudflareAPIError(f"Cloudflare request failed: {exc}") from exc

        if not 200 <= status < 300:
            raise CloudflareAPIError(f"Cloudflare HTTP {status}")
        result = _decode_json(raw, "Cloudflare", CloudflareAPIError)
        if not isinstance(result, Mapping):
            raise CloudflareAPIError("Cloudflare returned a non-object response")
        if result.get("success") is not True:
            raise CloudflareAPIError(f"Cloudflare API error: {_error_messages(result)}")
        return result

    def get_zone_id(self, domain: str) -> str:
        response = self._request("GET", "/zones", query={"name": domain})
        zones = response.get("result")
        if not isinstance(zones, list):
            raise CloudflareAPIError("Cloudflare zone lookup returned an invalid result")
        if not zones:
            raise ZoneNotFound(f"Cloudflare zone not found: {domain}")
        if len(zones) != 1 or not isinstance(zones[0], Mapping):
            raise CloudflareAPIError(
                f"Cloudflare zone lookup returned {len(zones)} matches for {domain}"
            )
        zone_id = zones[0].get("id")
        if not isinstance(zone_id, str) or not zone_id:
            raise CloudflareAPIError("Cloudflare zone lookup omitted the zone ID")
        return zone_id

    def upsert_record(
        self,
        zone_id: str,
        record_type: str,
        name: str,
        content: str,
        *,
        proxied: bool,
        match_content: bool = False,
    ) -> str:
        response = self._request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            query={"type": record_type, "name": name},
        )
        records = response.get("result")
        if not isinstance(records, list) or not all(
            isinstance(record, Mapping) for record in records
        ):
            raise CloudflareAPIError("Cloudflare DNS lookup returned an invalid result")

        desired = {
            "type": record_type,
            "name": name,
            "content": content,
            "proxied": proxied,
        }

        if match_content:
            exact = [record for record in records if record.get("content") == content]
            if exact:
                return "unchanged"
            self._request(
                "POST", f"/zones/{zone_id}/dns_records", payload=desired
            )
            return "created"

        if len(records) > 1:
            raise CloudflareAPIError(
                f"Cloudflare returned multiple {record_type} records for {name}"
            )
        if records:
            record = records[0]
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id:
                raise CloudflareAPIError("Cloudflare DNS record omitted its ID")
            if (
                record.get("content") == content
                and record.get("proxied") is proxied
                and record.get("type") == record_type
            ):
                return "unchanged"
            self._request(
                "PATCH",
                f"/zones/{zone_id}/dns_records/{record_id}",
                payload=desired,
            )
            return "updated"

        self._request("POST", f"/zones/{zone_id}/dns_records", payload=desired)
        return "created"


def _service_instance(
    status: Mapping[str, Any], environment_name: str, service_name: str
) -> tuple[str, str, str]:
    project_id = status.get("id")
    if not isinstance(project_id, str) or not project_id:
        raise RailwayStateError("Railway status omitted the project ID")

    environments = status.get("environments")
    edges = environments.get("edges") if isinstance(environments, Mapping) else None
    if not isinstance(edges, list):
        raise RailwayStateError("Railway status omitted environments")

    matching_environments = []
    for edge in edges:
        node = edge.get("node") if isinstance(edge, Mapping) else None
        if isinstance(node, Mapping) and node.get("name") == environment_name:
            matching_environments.append(node)
    if len(matching_environments) != 1:
        raise RailwayStateError(
            f"Railway environment {environment_name!r} was not found uniquely"
        )

    environment = matching_environments[0]
    environment_id = environment.get("id")
    instances = environment.get("serviceInstances")
    service_edges = instances.get("edges") if isinstance(instances, Mapping) else None
    if not isinstance(service_edges, list):
        raise RailwayStateError(
            f"Railway environment {environment_name!r} omitted service instances"
        )

    matches = []
    for edge in service_edges:
        node = edge.get("node") if isinstance(edge, Mapping) else None
        if isinstance(node, Mapping) and node.get("serviceName") == service_name:
            matches.append(node)
    if len(matches) != 1:
        raise RailwayStateError(
            f"Railway service {service_name!r} was not found uniquely in "
            f"environment {environment_name!r}"
        )

    service = matches[0]
    environment_id = service.get("environmentId") or environment_id
    service_id = service.get("serviceId")
    if not isinstance(environment_id, str) or not environment_id:
        raise RailwayStateError("Railway status omitted the selected environment ID")
    if not isinstance(service_id, str) or not service_id:
        raise RailwayStateError("Railway status omitted the selected service ID")
    return project_id, environment_id, service_id


def get_railway_domain_records(
    status: Mapping[str, Any],
    *,
    environment_name: str,
    service_name: str,
    domain: str,
    api_token: str,
    opener: Callable[..., Any] | None = None,
    graphql_url: str = DEFAULT_RAILWAY_GRAPHQL_URL,
) -> RailwayDomainRecords:
    if not api_token:
        raise RailwayAPIError("Railway API token is missing")
    project_id, environment_id, service_id = _service_instance(
        status, environment_name, service_name
    )
    query = """query Domains($environmentId:String!, $projectId:String!, $serviceId:String!) {
  domains(environmentId:$environmentId, projectId:$projectId, serviceId:$serviceId) {
    customDomains { domain status {
      dnsRecords { recordType requiredValue purpose }
      verificationToken verificationDnsHost
    } }
  }
}"""
    payload = {
        "query": query,
        "variables": {
            "environmentId": environment_id,
            "projectId": project_id,
            "serviceId": service_id,
        },
    }
    request = urllib.request.Request(
        graphql_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "User-Agent": "modelwatch-deploy",
            "x-source": "modelwatch-deploy",
        },
    )
    open_request = opener or _URL_OPEN
    try:
        with open_request(request, timeout=30) as response:
            status_code = getattr(response, "status", 200)
            raw = response.read()
    except HTTPError as exc:
        detail = _http_error_detail(exc, "Railway")
        raise RailwayAPIError(f"Railway HTTP {exc.code}: {detail}") from exc
    except (URLError, OSError) as exc:
        raise RailwayAPIError(f"Railway request failed: {exc}") from exc

    if not 200 <= status_code < 300:
        raise RailwayAPIError(f"Railway HTTP {status_code}")
    result = _decode_json(raw, "Railway", RailwayAPIError)
    if not isinstance(result, Mapping):
        raise RailwayAPIError("Railway returned a non-object response")
    if result.get("errors"):
        raise RailwayAPIError(f"Railway API error: {_error_messages(result)}")

    data = result.get("data")
    domains = data.get("domains") if isinstance(data, Mapping) else None
    custom_domains = (
        domains.get("customDomains") if isinstance(domains, Mapping) else None
    )
    if not isinstance(custom_domains, list):
        raise RailwayAPIError("Railway domain query returned an invalid result")
    matches = [
        item
        for item in custom_domains
        if isinstance(item, Mapping) and item.get("domain") == domain
    ]
    if not matches:
        raise RailwayDomainNotFound(f"Railway custom domain not found: {domain}")
    if len(matches) != 1:
        raise RailwayAPIError(f"Railway returned multiple custom domains for {domain}")

    domain_status = matches[0].get("status")
    if not isinstance(domain_status, Mapping):
        raise RailwayAPIError("Railway custom domain omitted its status")
    dns_records = domain_status.get("dnsRecords")
    if not isinstance(dns_records, list):
        raise RailwayAPIError("Railway custom domain omitted DNS records")
    cname_records = [
        record
        for record in dns_records
        if isinstance(record, Mapping)
        and record.get("recordType") == "DNS_RECORD_TYPE_CNAME"
    ]
    if len(cname_records) != 1:
        raise RailwayAPIError(
            f"Railway returned {len(cname_records)} CNAME requirements for {domain}"
        )
    cname = cname_records[0].get("requiredValue")
    if not isinstance(cname, str) or not cname:
        raise RailwayAPIError("Railway CNAME requirement omitted requiredValue")

    verification_host = domain_status.get("verificationDnsHost") or ""
    verification_token = domain_status.get("verificationToken") or ""
    if not isinstance(verification_host, str) or not isinstance(
        verification_token, str
    ):
        raise RailwayAPIError("Railway verification record was invalid")
    if bool(verification_host) != bool(verification_token):
        raise RailwayAPIError("Railway returned an incomplete verification record")
    return RailwayDomainRecords(cname, verification_host, verification_token)


def _normalize_verification_host(host: str, domain: str) -> str:
    if not host or host == domain or host.endswith(f".{domain}"):
        return host
    return f"{host}.{domain}"


def _load_status(path: str) -> Mapping[str, Any]:
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    try:
        status = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RailwayStateError("Railway CLI returned invalid status JSON") from exc
    if not isinstance(status, Mapping):
        raise RailwayStateError("Railway CLI status JSON was not an object")
    return status


def _railway_command(args: argparse.Namespace) -> int:
    records = get_railway_domain_records(
        _load_status(args.status_file),
        environment_name=args.environment,
        service_name=args.service,
        domain=args.domain,
        api_token=args.token or os.environ.get("RAILWAY_API_TOKEN", ""),
        graphql_url=os.environ.get(
            "RAILWAY_GRAPHQL_URL", DEFAULT_RAILWAY_GRAPHQL_URL
        ),
    )
    print(
        records.cname,
        records.verification_host,
        records.verification_token,
        sep="\t",
    )
    return 0


def _cloudflare_command(args: argparse.Namespace) -> int:
    client = CloudflareClient(
        args.token or os.environ.get("CLOUDFLARE_TOKEN", ""),
        base_url=os.environ.get(
            "CLOUDFLARE_API_BASE_URL", DEFAULT_CLOUDFLARE_API_BASE
        ),
    )
    zone_id = client.get_zone_id(args.domain)
    cname_action = client.upsert_record(
        zone_id,
        "CNAME",
        f"api.{args.domain}",
        args.cname,
        proxied=False,
    )
    print(f"[cloudflare] {cname_action} CNAME api.{args.domain}")
    if args.verify_host or args.verify_token:
        if not args.verify_host or not args.verify_token:
            raise CloudflareAPIError("Railway verification record is incomplete")
        verify_host = _normalize_verification_host(args.verify_host, args.domain)
        txt_action = client.upsert_record(
            zone_id,
            "TXT",
            verify_host,
            args.verify_token,
            proxied=False,
            match_content=True,
        )
        print(f"[cloudflare] {txt_action} TXT {verify_host}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    railway = subparsers.add_parser("railway")
    railway.add_argument("--status-file", default="-")
    railway.add_argument("--environment", required=True)
    railway.add_argument("--service", required=True)
    railway.add_argument("--domain", required=True)
    railway.add_argument("--token")
    railway.set_defaults(handler=_railway_command)

    cloudflare = subparsers.add_parser("cloudflare")
    cloudflare.add_argument("--domain", required=True)
    cloudflare.add_argument("--cname", required=True)
    cloudflare.add_argument("--verify-host", default="")
    cloudflare.add_argument("--verify-token", default="")
    cloudflare.add_argument("--token")
    cloudflare.set_defaults(handler=_cloudflare_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except ZoneNotFound as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except RailwayDomainNotFound as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    except DeployDNSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
