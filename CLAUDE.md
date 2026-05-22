# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A Claude Code **skill** repository. The only deliverable is `maya-mcp-builder.skill` — a ZIP archive that Claude Code loads to enable the `maya-mcp-builder` skill. The skill helps users build Autodesk Maya MCP server integrations for AI agents.

## Working with the skill file

The `.skill` file is a ZIP archive. To inspect or modify its contents, unzip it:

```powershell
Expand-Archive maya-mcp-builder.skill -DestinationPath maya-mcp-builder -Force
```

After editing, repackage:

```powershell
Compress-Archive -Path maya-mcp-builder\* -DestinationPath maya-mcp-builder.skill -Force
```

The internal structure must remain rooted at `maya-mcp-builder/` with `SKILL.md` at `maya-mcp-builder/SKILL.md`.

## Skill contents

```
maya-mcp-builder/
  SKILL.md                          # Skill definition and dispatch logic
  references/
    architecture.md                 # FastMCP + Maya threading model (required reading before changing scaffold)
    api-selection.md                # When to use maya.cmds vs OpenMaya
    agent-prompt-guide.md           # How to write/customize the agent system prompt
  assets/
    tool_template.py                # Canonical pattern for a single MCP tool
    agent_system_prompt.md          # Template for the LLM-facing Maya agent prompt
    server_scaffold/                # Complete runnable FastMCP server project
      maya_mcp/
        server.py                   # FastMCP instance, start_server(), _load_tools()
        bridge.py                   # run_main_thread() — the thread-safety bridge
        __init__.py
        userSetup.py                # Maya autoload entry point
        tools/
          scene.py                  # list_scene_objects, get_attribute, set_attribute, etc.
          mesh.py                   # get_mesh_summary, extrude_faces
          __init__.py
      requirements.txt              # mcp[cli]>=1.2.0
      pyproject.toml
      README.md
      claude_desktop_config.example.json
  scripts/
    parse_maya_doc.py               # CLI: fetch a help.autodesk.com page → tool stub
```

## Architecture of the generated server (critical)

The scaffold solves one hard constraint: **Maya's Python API is not thread-safe and must run on Maya's main thread**, but an MCP server must listen on a socket continuously without blocking that thread.

**Solution**: the server runs in a daemon thread; every tool body routes its Maya work through `bridge.run_main_thread()`, which calls `maya.utils.executeInMainThreadWithResult()` to schedule work on the main thread and block until it returns.

**Three rules all tools must follow** (violations cause crashes or scene corruption):

1. `import maya.*` goes **inside the inner `_do()` function**, not at module top level — tool modules are imported by the worker thread before Maya is in a safe state.
2. All Maya API calls go inside `_do()`, which is passed to `bridge.run_main_thread(_do)`.
3. Mutating tools wrap their work in `cmds.undoInfo(openChunk=True/closeChunk=True)` so the user can Ctrl+Z.

**Transport**: `streamable-http` on `localhost:6275` (default). Port is written to `~/.maya-mcp/port`. Clients connect via `mcp-remote` (see `claude_desktop_config.example.json`).

## Skill dispatch logic

`SKILL.md` routes requests into four buckets — match the request before generating anything:

| Bucket | When | Output |
|---|---|---|
| Full project | No existing scaffold | Full `server_scaffold/` + agent prompt |
| Add a tool | Scaffold exists, wrapping a specific command | Single tool file using `tool_template.py` |
| Parse a doc page | `help.autodesk.com` URL or command name given | Run `parse_maya_doc.py` → tool stub |
| Agent prompt only | Server already exists | Customized `agent_system_prompt.md` |

`parse_maya_doc.py` handles two URL families: `CommandsPython/<cmd>.html` and `py_ref/class_<class>.html`. It falls back to `urllib` + regex if `requests`/`beautifulsoup4` are unavailable.
