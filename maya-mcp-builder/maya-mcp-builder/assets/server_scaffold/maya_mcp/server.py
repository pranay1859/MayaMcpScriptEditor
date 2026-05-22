"""FastMCP server that runs inside Maya as a daemon thread.

Lifecycle:
    1. `userSetup.py` imports this module on Maya startup.
    2. `start_server()` registers all tools, picks a port, and launches
       a daemon thread that runs the asyncio event loop forever.
    3. Tool calls arrive on that thread; each tool hops to Maya's main
       thread via `bridge.run_main_thread` before touching the Maya API.
    4. The daemon dies when Maya exits.

Transport is `streamable-http`. The chosen host/port is written to
``~/.maya-mcp/port`` so clients can discover it.

Remote access
-------------
By default the server binds to ``127.0.0.1`` (localhost only).
To allow remote connections from other machines on your network, set:

    MAYA_MCP_HOST=0.0.0.0   # or a specific interface IP
    MAYA_MCP_PORT=6275       # optional, defaults to 6275

**Security**: binding to ``0.0.0.0`` exposes the port to everyone on
the network. Use your studio VPN or a firewall rule to restrict access
to trusted hosts. No authentication is built in.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("maya_mcp")

# ---- module state -----------------------------------------------------

mcp: FastMCP = FastMCP("maya-mcp")
"""Shared FastMCP application. Tool modules import this and use ``@mcp.tool()``."""

_server_thread: threading.Thread | None = None
_chosen_port: int | None = None
_chosen_host: str | None = None

DEFAULT_PORT = int(os.environ.get("MAYA_MCP_PORT", "6275"))
DEFAULT_HOST = os.environ.get("MAYA_MCP_HOST", "127.0.0.1")
PORT_FILE = Path.home() / ".maya-mcp" / "port"


# ---- public API -------------------------------------------------------

def start_server(port: int | None = None, host: str | None = None) -> int:
    """Start the MCP server on a daemon thread. Idempotent.

    ``host`` and ``port`` default to the ``MAYA_MCP_HOST`` /
    ``MAYA_MCP_PORT`` environment variables (fallback: ``127.0.0.1:6275``).

    Returns the chosen port. Writes ``<host>:<port>`` to
    ``~/.maya-mcp/port`` so external clients can discover the address.
    """
    global _server_thread, _chosen_port, _chosen_host

    if _server_thread is not None and _server_thread.is_alive():
        logger.info("maya-mcp server already running on port %s", _chosen_port)
        return _chosen_port  # type: ignore[return-value]

    _configure_logging()
    _load_tools()

    resolved_host = host if host is not None else DEFAULT_HOST
    chosen = port if port is not None else _find_free_port(DEFAULT_PORT, resolved_host)
    _chosen_port = chosen
    _chosen_host = resolved_host
    _write_port_file(resolved_host, chosen)

    def _run() -> None:
        # FastMCP picks up host/port via these attributes (kept compatible
        # across SDK versions; if your `mcp` version uses different names,
        # adjust here — the SDK is still pre-1.0).
        mcp.settings.host = resolved_host  # type: ignore[attr-defined]
        mcp.settings.port = chosen  # type: ignore[attr-defined]
        try:
            asyncio.run(mcp.run_streamable_http_async())
        except Exception:  # noqa: BLE001
            logger.exception("MCP server thread crashed")

    _server_thread = threading.Thread(
        target=_run,
        name="maya-mcp-server",
        daemon=True,
    )
    _server_thread.start()
    logger.info("maya-mcp listening on http://%s:%d/mcp", resolved_host, chosen)
    return chosen


def get_port() -> int | None:
    """Return the port the server is listening on, or None if not started."""
    return _chosen_port


def get_host() -> str | None:
    """Return the host the server is bound to, or None if not started."""
    return _chosen_host


# ---- internals --------------------------------------------------------

def _load_tools() -> None:
    """Import every tool module so its decorators register with `mcp`."""
    # Order doesn't matter for registration; each module decorates `mcp`.
    from maya_mcp.tools import scene, mesh, td, rigging, animation, export, rendering, scene_mgmt  # noqa: F401

    # If you add a new tool module, import it here.


def _find_free_port(start: int, host: str) -> int:
    """Return the first free port at or after ``start`` on ``host``."""
    port = start
    for _ in range(64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                port += 1
    raise RuntimeError(f"No free port found in [{start}, {start + 64})")


def _write_port_file(host: str, port: int) -> None:
    try:
        PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        PORT_FILE.write_text(f"{host}:{port}", encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write port file %s: %s", PORT_FILE, exc)


def _configure_logging() -> None:
    """Send logs to Maya's Script Editor and a rotating file."""
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)

    # Console (Maya Script Editor)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("[maya-mcp] %(message)s"))
    logger.addHandler(sh)

    # Rotating file
    try:
        from logging.handlers import RotatingFileHandler

        log_dir = Path.home() / ".maya-mcp"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "server.log", maxBytes=1_000_000, backupCount=3
        )
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(fh)
    except Exception:  # noqa: BLE001
        logger.warning("Could not set up file logging", exc_info=True)
