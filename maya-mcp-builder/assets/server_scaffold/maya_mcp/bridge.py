"""Bridge between the FastMCP worker thread and Maya's main thread.

Every tool body MUST funnel its Maya API work through `run_main_thread`.
Calling Maya from any other thread is unsafe (segfaults, scene corruption,
silently wrong query results).

See `references/architecture.md` in the maya-mcp-builder skill for why.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")

_log_lock = threading.Lock()


def run_main_thread(fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Execute ``fn(*args, **kwargs)`` on Maya's main thread.

    Blocks the calling thread until the work completes. Exceptions raised
    inside ``fn`` are re-raised in the caller with the original traceback
    preserved as a chained cause, so MCP error responses include the real
    Maya error rather than a generic "main thread call failed".

    Must be called from a non-main thread (the FastMCP worker). If called
    from the main thread, falls through to a direct call so unit tests
    outside Maya still work.
    """
    # Outside Maya (e.g. unit tests, type checkers), just call directly.
    try:
        from maya import utils  # type: ignore[import-not-found]
    except ImportError:
        return fn(*args, **kwargs)

    # On the main thread already? Just call directly — the executor
    # would deadlock otherwise.
    if threading.current_thread() is threading.main_thread():
        return fn(*args, **kwargs)

    # Capture the exception so we can re-raise on the calling thread.
    box: dict[str, Any] = {}

    def _wrapped() -> _T | None:
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — we re-raise below
            box["exc"] = exc
            box["tb"] = traceback.format_exc()
            return None

    result = utils.executeInMainThreadWithResult(_wrapped)

    if "exc" in box:
        # Log on the worker side too so it shows up in the MCP logs,
        # and re-raise with the original traceback chained.
        with _log_lock:
            _log_error(box["tb"])
        raise box["exc"]
    return result  # type: ignore[return-value]


def _log_error(tb: str) -> None:
    """Best-effort logging of a tool exception."""
    try:
        import logging

        logging.getLogger("maya_mcp").error("Tool raised on main thread:\n%s", tb)
    except Exception:
        # Logging must never break the bridge.
        pass
