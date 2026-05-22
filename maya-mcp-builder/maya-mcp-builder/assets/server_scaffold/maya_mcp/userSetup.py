"""Maya autoload script.

Maya runs ``userSetup.py`` on startup if it's anywhere on the user's
PYTHONPATH or in their Maya scripts directory. This file launches the
MCP server in deferred mode so we don't block Maya's startup.

If you'd rather start the server manually (e.g. from a shelf button),
remove the `executeDeferred` line and call `maya_mcp.start_server()`
from Python directly when you want it.

Remote access
-------------
Set these env vars *before* launching Maya to allow connections from
other machines (e.g. teammates running Claude Desktop on their own PCs):

    MAYA_MCP_HOST=0.0.0.0   # bind all interfaces; use specific IP to restrict
    MAYA_MCP_PORT=6275       # optional override

On Windows you can set these in Maya.env or via System Properties → Environment Variables.
On Linux/macOS, export them in the shell that launches Maya.

**Security**: only do this inside a studio VPN or with a firewall rule.
No authentication is built in — anyone who can reach the port can call tools.
"""
from __future__ import annotations


def _launch_maya_mcp() -> None:
    try:
        import maya_mcp

        # host/port come from MAYA_MCP_HOST / MAYA_MCP_PORT env vars
        # (see server.py). Passing None here lets server.py read them.
        maya_mcp.start_server()
    except Exception:  # noqa: BLE001
        # Never let a broken MCP setup prevent Maya from starting.
        import traceback

        traceback.print_exc()


# Defer so Maya's UI is fully initialized before we touch logging.
try:
    from maya import cmds, utils  # type: ignore[import-not-found]

    if not cmds.about(batch=True):
        utils.executeDeferred(_launch_maya_mcp)
    else:
        # In batch mode, just call it directly.
        _launch_maya_mcp()
except ImportError:
    # Not running inside Maya (e.g. unit tests importing the package).
    pass
