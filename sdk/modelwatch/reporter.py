"""Batch reporter for tool-call telemetry.

Records are queued in memory and flushed by a background thread. Failures are
swallowed. With no API key, or with an endpoint that is not http(s), the
reporter sends nothing.
"""

from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger("modelwatch.sdk")
logger.addHandler(logging.NullHandler())

DEFAULT_ENDPOINT = "https://api.modelwatch.app"
INGEST_PATH = "/api/tools/ingest"
DEFAULT_BATCH_SIZE = 50
DEFAULT_FLUSH_INTERVAL = 5.0
DEFAULT_QUEUE_LIMIT = 5000

_UNSET = object()


def ingest_url(endpoint: Optional[str]) -> Optional[str]:
    """Build the ingest URL, or return None when the endpoint is unusable."""
    raw = (endpoint or "").strip().rstrip("/")
    if not raw.startswith("https://") and not raw.startswith("http://"):
        return None
    return f"{raw}{INGEST_PATH}"


@dataclass
class _Config:
    api_key: Optional[str] = None
    endpoint: str = DEFAULT_ENDPOINT
    batch_size: int = DEFAULT_BATCH_SIZE
    flush_interval: float = DEFAULT_FLUSH_INTERVAL
    queue_limit: int = DEFAULT_QUEUE_LIMIT
    enabled: bool = True


def _key_from_env() -> Optional[str]:
    value = (os.getenv("MODELWATCH_API_KEY") or "").strip()
    return value or None


_config = _Config(
    api_key=_key_from_env(),
    endpoint=os.getenv("MODELWATCH_API_BASE", DEFAULT_ENDPOINT) or DEFAULT_ENDPOINT,
)


def configure(
    api_key: Any = _UNSET,
    endpoint: Any = _UNSET,
    batch_size: Optional[int] = None,
    flush_interval: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> None:
    """Override configuration. Passing api_key=None disables reporting."""
    if api_key is not _UNSET:
        _config.api_key = (str(api_key).strip() or None) if api_key else None
    if endpoint is not _UNSET:
        _config.endpoint = str(endpoint or "").strip()
    if batch_size is not None:
        _config.batch_size = batch_size
    if flush_interval is not None:
        _config.flush_interval = flush_interval
    if enabled is not None:
        _config.enabled = enabled


@dataclass
class _Record:
    tool_name: str
    agent_id: str
    latency_ms: int
    success: bool
    error: Optional[str]
    output_shape: Optional[str]
    output_shape_tree: Optional[Any]
    tags: dict = field(default_factory=dict)
    called_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ModelWatchReporter:
    """Singleton reporter. Sync and async callers both enqueue and return."""

    _instance: Optional["ModelWatchReporter"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "ModelWatchReporter":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._queue: queue.Queue[_Record] = queue.Queue(maxsize=_config.queue_limit)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="modelwatch-reporter",
            daemon=True,
        )
        self._thread.start()
        atexit.register(self._shutdown)

    def record(
        self,
        tool_name: str,
        agent_id: Optional[str],
        latency_ms: int,
        success: bool,
        error: Optional[str],
        output_shape: Optional[str],
        output_shape_tree: Optional[Any] = None,
        tags: Optional[dict] = None,
    ) -> None:
        """Enqueue a record. Never raises."""
        if not _config.enabled or not _config.api_key or ingest_url(_config.endpoint) is None:
            return
        try:
            rec = _Record(
                tool_name=tool_name,
                agent_id=agent_id or "default",
                latency_ms=latency_ms,
                success=success,
                error=error,
                output_shape=output_shape,
                output_shape_tree=output_shape_tree,
                tags=tags or {},
            )
            self._queue.put_nowait(rec)
        except queue.Full:
            logger.debug("modelwatch tool queue full, dropping record")
        except Exception as exc:
            logger.debug("modelwatch tool record failed: %s", exc)

    def _worker_loop(self) -> None:
        last_flush = time.time()
        batch: list[_Record] = []
        while not self._stop.is_set():
            timeout = max(0.05, _config.flush_interval - (time.time() - last_flush))
            try:
                rec = self._queue.get(timeout=timeout)
                batch.append(rec)
            except queue.Empty:
                pass

            now = time.time()
            should_flush = (
                len(batch) >= _config.batch_size
                or (batch and now - last_flush >= _config.flush_interval)
            )
            if should_flush:
                self._flush(batch)
                batch = []
                last_flush = now

        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._flush(batch)

    def _flush(self, batch: list[_Record]) -> None:
        if not batch:
            return
        url = ingest_url(_config.endpoint)
        api_key = _config.api_key
        if not url or not api_key:
            return
        payload = [
            {
                "tool_name": rec.tool_name,
                "agent_id": rec.agent_id,
                "latency_ms": rec.latency_ms,
                "success": rec.success,
                "error": rec.error,
                "output_shape": rec.output_shape,
                "output_shape_tree": rec.output_shape_tree,
                "tags": rec.tags,
                "called_at": rec.called_at,
            }
            for rec in batch
        ]
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if resp.status_code >= 400:
                    logger.debug("modelwatch tool ingest non-2xx: %s", resp.status_code)
        except Exception as exc:
            logger.debug("modelwatch tool ingest failed: %s", type(exc).__name__)

    def _shutdown(self) -> None:
        self._stop.set()
        try:
            self._thread.join(timeout=3.0)
        except Exception:
            pass


def get_reporter() -> ModelWatchReporter:
    return ModelWatchReporter()
