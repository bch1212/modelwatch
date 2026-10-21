"""ModelWatch tool monitoring.

One decorator records latency, success, and response shape for agent tool
calls. Shape fingerprints are compared by the ModelWatch API, which alerts
when a tool's response schema drifts.

    import os
    os.environ["MODELWATCH_API_KEY"] = "mw_..."

    from modelwatch import monitor

    @monitor(tool_name="search_web", agent_id="my-agent")
    async def search_web(query: str) -> dict:
        ...
"""

from modelwatch.decorator import monitor
from modelwatch.mcp import wrap_mcp_server
from modelwatch.reporter import ModelWatchReporter, configure
from modelwatch.schema import fingerprint

__version__ = "0.1.0"
__all__ = [
    "monitor",
    "ModelWatchReporter",
    "configure",
    "fingerprint",
    "wrap_mcp_server",
]
