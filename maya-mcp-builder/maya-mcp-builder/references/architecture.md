# Architecture: FastMCP inside Maya

This document explains *why* the scaffold is shaped the way it is. Read it once before generating the scaffold for a new user. Don't change the threading model or transport without understanding what's here — the constraints come from Maya, not from style preferences.

## The core problem: Maya's main thread

Maya's Python API (`maya.cmds`, `maya.api.OpenMaya`, every plugin entry point) is **not thread-safe**. It must be called from the main thread — the same thread that drives Maya's UI and DG (dependency graph) evaluation. Calling from any other thread can:

- Crash Maya outright (segfault on next DG eval).
- Silently corrupt the scene (a half-applied edit that no one sees until save).
- Produce wrong results (queries returning stale data because the DG hasn't ticked).

An MCP server, on the other hand, has to **listen on a socket** continuously. It cannot block Maya's main thread to do that, because then Maya freezes — no viewport, no menus, no input.

So we need two threads:
1. A **worker thread** running the MCP server + its event loop, accepting tool calls.
2. The **main thread** (Maya's), which actually executes the Maya work.

And a way to safely hand work from #1 to #2 and get a result back.

## The bridge: `executeInMainThreadWithResult`

Maya ships `maya.utils.executeInMainThreadWithResult`. You call it from a worker thread; it schedules a callable to run on the main thread at the next idle event, waits, and returns the result (or raises the exception). This is exactly what we need.

The scaffold wraps this in `bridge.py`:

```python
# bridge.py
from maya import utils

def run_main_thread(fn, /, *args, **kwargs):
    """Run `fn(*args, **kwargs)` on Maya's main thread, return the result.

    Exceptions raised inside `fn` propagate back to the caller.
    Safe to call from any non-main thread (including FastMCP's worker).
    """
    def _wrapped():
        return fn(*args, **kwargs)
    return utils.executeInMainThreadWithResult(_wrapped)
```

Every tool body uses this:

```python
@mcp.tool()
def create_cube(name: str = "pCube1", size: float = 1.0) -> str:
    """Create a polygonal cube and return its node name."""
    def _do():
        import maya.cmds as cmds  # import INSIDE — see "Why imports go inside" below
        result = cmds.polyCube(name=name, width=size, height=size, depth=size)
        return result[0]
    return bridge.run_main_thread(_do)
```

### Why imports go inside the function

`tools/*.py` modules are imported by the MCP worker thread when FastMCP collects tool decorators. That happens **before** Maya guarantees any module is in a safe state, and on a non-main thread. Doing `import maya.cmds` at module top level can fail (depends on Maya's plugin load order) or, worse, leave `cmds` pointing at a half-initialized module. Importing inside the function defers the import to the main thread, where it's safe.

There's a small perf cost (one `sys.modules` lookup per call), but it's nanoseconds compared to a Maya command's actual work.

## Transport: `streamable-http`, not stdio

The `mcp` SDK supports three transports:

- **stdio** — server reads/writes on stdin/stdout. Standard for CLI-spawned MCP servers.
- **sse** — Server-Sent Events over HTTP. Older HTTP transport.
- **streamable-http** — modern HTTP transport with bidirectional streaming.

**stdio is wrong for in-Maya.** Maya owns stdin/stdout (logs go there, Python `print` goes there, the Script Editor reads from there). If the MCP server tries to use them, you get garbled output in both directions.

**streamable-http is the right default.** The MCP server listens on `localhost:PORT`. Maya keeps its stdio. Claude Desktop connects either:

1. **Via `mcp-remote`** — a small npx-installable bridge that exposes a remote HTTP MCP server as if it were a local stdio one. Claude Desktop config:
   ```json
   {
     "mcpServers": {
       "maya": {
         "command": "npx",
         "args": ["-y", "mcp-remote", "http://localhost:6275/mcp"]
       }
     }
   }
   ```
2. **Direct HTTP** if the client supports it natively.

The scaffold defaults to port `6275` (a stable hash of "maya-mcp" mod a private port range) but falls back to the next free port and writes the chosen one to `~/.maya-mcp/port` for the config to pick up.

## Server lifecycle

```
Maya startup
   │
   ▼
userSetup.py runs (Maya autoload)
   │
   ▼
start_server() called once
   │
   ├──► Spawns daemon thread T
   │       │
   │       ▼
   │    asyncio.run(mcp.run_streamable_http_async())
   │    Worker thread accepts requests forever.
   │
   ▼
Maya main thread continues normally.

For each MCP tool call:
   │
   FastMCP worker thread receives request
   │
   ▼
   Tool function body runs (on worker thread)
   │
   ▼
   bridge.run_main_thread(_do) blocks worker, schedules _do on main
   │
   ▼
   Maya main thread executes _do at next idle event
   │
   ▼
   Result returns to worker, FastMCP serializes back to client.
```

The daemon thread dies when Maya exits — no shutdown hook needed for the common path.

## Why not run FastMCP synchronously on Maya's main thread?

You can't. `mcp.run()` blocks forever in an asyncio event loop. If you call it on the main thread, Maya's event loop never runs and the UI hangs. The asyncio loop has to live on a thread that isn't Maya's.

## Why not the command-port approach (PatrickPalmer/MayaMCP)?

That design runs the MCP server in a separate process that talks to Maya over Maya's MEL command port. It's a valid choice and has real virtues:

- The MCP process is a normal stdio MCP server (no Maya plugin involved).
- You can restart the MCP server without restarting Maya.

But it has trade-offs:
- Two round trips per command (Maya's command port returns nothing from multi-line Python, so you have to ping twice — once to run, once to read a result variable).
- Code goes over the wire as MEL-wrapped Python strings.
- Type safety is weaker (everything is strings + JSON-decode).

The user picked FastMCP-inside-Maya specifically. Stick with it. If they later want the command-port style, that's a different skill or a different scaffold variant.

## When something feels wrong, check these first

These are the failure modes we've seen repeatedly:

| Symptom | Likely cause |
|---|---|
| Maya crashes on first tool call | Tool body called Maya API without `run_main_thread` |
| `ImportError: No module named maya.cmds` at server start | `import maya.cmds` at module top level instead of inside the function |
| Server starts but Claude can't connect | `mcp-remote` URL doesn't match the port written to `~/.maya-mcp/port` |
| Tool call hangs forever | Tool body opened an undo chunk and never closed it, or queried a node that doesn't exist with no error handling |
| Wrong values returned by queries | DG hasn't evaluated yet; insert `cmds.dgdirty(allPlugs=True)` or query after `cmds.refresh()` |
| Two Maya sessions both want port 6275 | The port-fallback logic worked; check the second session's `~/.maya-mcp/port` |

## Versioning

- Maya 2024 → Python 3.10. First version where the modern `mcp` SDK installs cleanly.
- Maya 2025 → Python 3.11.
- Maya 2026 → Python 3.11.
- Maya 2027 → Python 3.11.

If the user is on Maya 2022 or 2023 (Python 3.7), the `mcp` SDK won't install — they'd need to backport or use the command-port architecture instead. Flag this immediately rather than letting them hit it during pip install.
