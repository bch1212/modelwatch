"""@monitor must never crash or block the caller.

Ported from ToolPulse's decorator tests, plus ModelWatch transport checks.
"""

import asyncio
import os

import pytest

os.environ.pop("MODELWATCH_API_KEY", None)

from modelwatch import configure, monitor, wrap_mcp_server  # noqa: E402
from modelwatch.reporter import (  # noqa: E402
    DEFAULT_ENDPOINT,
    INGEST_PATH,
    _Record,
    get_reporter,
    ingest_url,
)
from modelwatch.schema import extract_shape  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet_reporter():
    configure(api_key=None, endpoint=DEFAULT_ENDPOINT, enabled=True)
    yield
    configure(api_key=None, endpoint=DEFAULT_ENDPOINT, enabled=True)


def test_sync_function_returns_unchanged():
    @monitor(tool_name="adder")
    def add(a, b):
        return a + b

    assert add(2, 3) == 5


def test_sync_function_exception_propagates():
    @monitor(tool_name="boom")
    def boom():
        raise ValueError("expected")

    with pytest.raises(ValueError, match="expected"):
        boom()


@pytest.mark.asyncio
async def test_async_function_returns_unchanged():
    @monitor(tool_name="async_add")
    async def add(a, b):
        await asyncio.sleep(0)
        return a + b

    assert await add(2, 3) == 5


@pytest.mark.asyncio
async def test_async_function_exception_propagates():
    @monitor(tool_name="async_boom")
    async def boom():
        raise RuntimeError("expected")

    with pytest.raises(RuntimeError, match="expected"):
        await boom()


def test_decorator_never_crashes_with_unserializable_response():
    class Unserializable:
        def __repr__(self):
            return "<weird>"

    @monitor(tool_name="weird")
    def weird():
        return Unserializable()

    result = weird()
    assert isinstance(result, Unserializable)


def test_decorator_works_when_api_key_unset():
    configure(api_key=None)

    @monitor(tool_name="silent")
    def echo(x):
        return x

    assert echo(42) == 42


def test_decorator_records_shape_not_values(monkeypatch):
    recorded = {}

    class _Fake:
        def record(self, **kwargs):
            recorded.update(kwargs)

    monkeypatch.setattr("modelwatch.decorator.get_reporter", lambda: _Fake())

    @monitor(tool_name="search")
    def search():
        return {"secret": "hunter2", "n": 1}

    assert search()["secret"] == "hunter2"
    assert recorded["success"] is True
    assert recorded["output_shape"]
    assert "hunter2" not in str(recorded["output_shape_tree"])
    assert recorded["output_shape_tree"] == extract_shape({"secret": "hunter2", "n": 1})


def test_default_ingest_url_is_modelwatch():
    assert DEFAULT_ENDPOINT == "https://api.modelwatch.app"
    assert ingest_url(DEFAULT_ENDPOINT) == "https://api.modelwatch.app/api/tools/ingest"
    assert INGEST_PATH == "/api/tools/ingest"
    assert ingest_url("") is None
    assert ingest_url("ftp://api.modelwatch.app") is None
    assert "toolpulse" not in DEFAULT_ENDPOINT


def test_reporter_posts_bearer_token_to_modelwatch(httpx_mock):
    httpx_mock.add_response(status_code=200)
    configure(api_key="mw_test_key", endpoint="https://api.modelwatch.app", enabled=True)
    get_reporter()._flush([
        _Record(
            tool_name="search",
            agent_id="agent",
            latency_ms=4,
            success=True,
            error=None,
            output_shape="abc123abc123abcd",
            output_shape_tree={"id": "int"},
            tags={},
        )
    ])
    request = httpx_mock.get_requests()[0]
    assert str(request.url) == "https://api.modelwatch.app/api/tools/ingest"
    assert request.headers["authorization"] == "Bearer mw_test_key"
    assert "x-api-key" not in {k.lower() for k in request.headers}
    body = request.content.decode()
    assert "hunter2" not in body
    assert "toolpulse" not in str(request.url)


def test_reporter_does_not_send_without_a_key(httpx_mock):
    configure(api_key=None, endpoint="https://api.modelwatch.app", enabled=True)
    get_reporter().record(
        tool_name="search",
        agent_id="agent",
        latency_ms=1,
        success=True,
        error=None,
        output_shape=None,
    )
    assert httpx_mock.get_requests() == []


def test_wrap_mcp_server_preserves_return_value():
    class _Tool:
        def __init__(self, fn):
            self.fn = fn

    class _Manager:
        def __init__(self):
            self._tools = {"search": _Tool(lambda q: {"q": q})}

    class _Server:
        def __init__(self):
            self._tool_manager = _Manager()

    server = _Server()
    wrap_mcp_server(server, agent_id="bot")
    assert server._tool_manager._tools["search"].fn("hi") == {"q": "hi"}


def test_wrap_mcp_server_requires_a_tool_manager():
    with pytest.raises(RuntimeError, match="tool manager"):
        wrap_mcp_server(object())
