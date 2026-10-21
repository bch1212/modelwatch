"""@monitor decorator — wraps any tool function (async or sync).

The decorator never crashes or blocks the caller.
- Network I/O is handed to a background thread
- Exceptions inside the monitoring code are swallowed
- Exceptions from the wrapped function are re-raised unchanged
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
from typing import Any, Callable, Optional

from modelwatch.reporter import get_reporter
from modelwatch.schema import extract_shape, fingerprint


def monitor(
    tool_name: Optional[str] = None,
    agent_id: Optional[str] = None,
    tags: Optional[dict] = None,
    capture_shape: bool = True,
):
    """Record latency, success, and response shape for a tool call.

    Args:
        tool_name: name to attribute the call to. Defaults to the function name.
        agent_id: which agent made the call. Defaults to "default".
        tags: small key/value metadata attached to every call.
        capture_shape: skip fingerprinting when False.

    Example:
        @monitor(tool_name="search_web", agent_id="researcher")
        async def search_web(query: str) -> dict:
            ...
    """

    def decorator(func: Callable):
        name = tool_name or getattr(func, "__name__", "anonymous")
        is_async = asyncio.iscoroutinefunction(func) or inspect.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start = time.perf_counter()
                error: Optional[str] = None
                result: Any = None
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as exc:
                    error = _format_error(exc)
                    raise
                finally:
                    _record_safe(name, agent_id, start, result, error, tags, capture_shape)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            error: Optional[str] = None
            result: Any = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as exc:
                error = _format_error(exc)
                raise
            finally:
                _record_safe(name, agent_id, start, result, error, tags, capture_shape)

        return sync_wrapper

    return decorator


def _format_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def _record_safe(
    name: str,
    agent_id: Optional[str],
    start: float,
    result: Any,
    error: Optional[str],
    tags: Optional[dict],
    capture_shape: bool,
) -> None:
    """Never raise from the decorator's finally block."""
    try:
        latency_ms = int((time.perf_counter() - start) * 1000)
        shape: Optional[str] = None
        shape_tree: Optional[Any] = None
        if capture_shape and result is not None and error is None:
            try:
                shape_tree = extract_shape(result)
                shape = fingerprint(result)
            except Exception:
                pass
        get_reporter().record(
            tool_name=name,
            agent_id=agent_id,
            latency_ms=latency_ms,
            success=error is None,
            error=error,
            output_shape=shape,
            output_shape_tree=shape_tree,
            tags=tags,
        )
    except Exception:
        pass
