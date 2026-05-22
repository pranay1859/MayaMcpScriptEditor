---
name: maya-mcp-builder
description: Build Autodesk Maya integrations for AI agents — MCP servers, individual MCP tools, and Maya-agent system prompts. Use this skill whenever the user wants to wrap a Maya command/API as an MCP tool, generate an MCP server that runs inside Maya (FastMCP + maya.cmds + OpenMaya), write a system prompt for an LLM that drives Maya, or convert a Maya help page (help.autodesk.com Commands/Python API reference) into working tool code. Trigger this skill even when the user says things like "I want Claude to control Maya", "wrap polyExtrudeFace as a tool", "make an MCP for our rigging pipeline", "agent that builds scenes in Maya", or references files like userSetup.py, maya.cmds, OpenMaya, MFnMesh, or pastes a help.autodesk.com URL. The skill handles both maya.cmds (Python Commands) and OpenMaya (py_ref) APIs and produces a runnable FastMCP scaffold plus a tuned agent system prompt.
---

# Maya MCP Builder

This skill helps you build Maya integrations for AI agents. It produces two deliverables — usually together:

1. **An MCP server scaffold** that runs inside Maya, exposes Maya commands as MCP tools, and safely marshals every call onto Maya's main thread.
2. **An agent system prompt** that teaches the LLM how to actually use those tools well (selection model, undo discipline, when to use `cmds` vs OpenMaya, common pitfalls).

The scaffold targets the **official `mcp` Python SDK with FastMCP**, running as a daemon thread inside Maya's Python interpreter and serving over `streamable-http`. This is different from command-port designs (e.g. PatrickPalmer/MayaMCP) — the user picked this explicitly. Don't switch architectures without asking.

---

## Decide what the user actually wants

Most requests fall into one of these four buckets. Match the request to a bucket, then go straight to the relevant section. If the request is ambiguous, ask one short question before generating anything.

| Bucket | Signals | Output |
|---|---|---|
| **Full project** | "set up an MCP for Maya", "I want Claude to control Maya", no existing scaffold | Generate the full scaffold from `assets/server_scaffold/` + an agent prompt from `assets/agent_system_prompt.md`. |
| **Add a tool** | They already have a scaffold; "wrap polyExtrudeFace", "add a tool for X" | Generate a single tool file using `assets/tool_template.py` and the parsing workflow below. |
| **Parse a doc page** | They paste a `help.autodesk.com` URL or a command name | Run `scripts/parse_maya_doc.py` to fetch + parse, then turn the result into a tool stub. |
| **Agent prompt only** | "write a system prompt for a Maya agent", they already have the server | Customize `assets/agent_system_prompt.md` for their pipeline. |

Don't dump everything in `assets/` into the output if they only asked for one tool. Skills should produce what was asked for and a little more, not the kitchen sink.

---

## The architecture you'll be generating (and why)

You should read `references/architecture.md` once before producing the scaffold for the first time — it has the threading model that everything depends on. The short version:

- **FastMCP runs in a background thread inside Maya.** Maya's main thread is the only place the Maya API is safe to touch. The scaffold uses `maya.utils.executeInMainThreadWithResult` to hop every tool call onto the main thread, then returns the result back to the FastMCP worker thread.
- **Transport is `streamable-http`, not stdio.** Maya owns stdin/stdout; an HTTP server on `localhost` is the path of least resistance. Claude Desktop connects via `mcp-remote` or the native HTTP connector.
- **Tools are organized by Maya subsystem**, not all in one file. `tools/scene.py`, `tools/mesh.py`, `tools/rigging.py`, etc. The package's `__init__` registers each module's tools with the FastMCP app.
- **Bootstrap via `userSetup.py`.** Maya auto-runs this on startup; it launches the MCP thread once per session.

When you explain this to the user, lead with *why* the threading hop exists. Without it, the Maya API will crash or silently corrupt scene state on the first tool call. That's the single biggest footgun in this whole setup.

---

## Workflow: generating a new full project

1. **Confirm version + Python.** Ask which Maya version they're on (Maya 2024+ ships Python 3.10+, which is required by the `mcp` SDK). Maya 2026 and 2027 are the current versions on help.autodesk.com.
2. **Copy the scaffold** from `assets/server_scaffold/` to the user's working directory. Adjust the package name if they want something other than `maya_mcp`. Don't rewrite the bridge or server unless they ask — those files encode the threading model.
3. **Generate starter tools.** Read the user's brief and either (a) seed `tools/scene.py` and `tools/mesh.py` with 4–6 commonly useful commands from the doc references, or (b) ask which subsystem to start with.
4. **Generate the agent system prompt.** Start from `assets/agent_system_prompt.md`. Fill in the placeholders for pipeline-specific conventions (unit system, naming convention, output paths). If you don't know, leave a clearly-marked TODO and tell the user.
5. **Print installation instructions.** Where to drop the module so Maya finds it (`MAYA_MODULE_PATH` or `Documents/maya/<version>/modules`), how to register the Claude Desktop config, and how to verify the server started (look for the log line in Maya's Script Editor).
6. **Validate.** Run `quick_validate.py` if it's available, or eyeball the scaffold for: every tool has a type-annotated signature, every tool has a docstring, every tool's body hops to main thread.

---

## Workflow: wrapping a single Maya command as a tool

Use this when the user names one command (e.g. "wrap `polyExtrudeFace`") or pastes a Maya help URL.

1. **Fetch the doc.** If they gave a URL, run `python scripts/parse_maya_doc.py <url>`. The script handles both `help.autodesk.com/cloudhelp/<year>/.../CommandsPython/<cmd>.html` (Commands reference) and the `py_ref/` OpenMaya pages. If they only gave a command name, search `help.autodesk.com` for the canonical page first.
2. **Read the parsed output.** You'll get: command name, summary, return type, a list of flags (long name, short name, type, properties like `[C]reate/[Q]uery/[E]dit/[M]ulti-use`), and example snippets.
3. **Pick the API tier.** See `references/api-selection.md`. Default to `maya.cmds`; reach for OpenMaya when the doc says the command is slow, or when the task is mesh data manipulation, custom DG nodes, or anything performance-critical.
4. **Generate the tool** using `assets/tool_template.py`. Key rules:
    - Function name matches the FastMCP tool name; make it descriptive (`extrude_polygon_face` is better than `polyExtrudeFace_wrapper`).
    - Every parameter has a Python type hint *and* a default value where the Maya flag has one. The MCP SDK uses these to build the JSON schema the LLM sees.
    - The docstring is the tool description shown to the LLM. Lead with what the tool *does*, not what flag it sets. Include 1–2 example calls in the docstring — agents lean on these heavily.
    - The body imports `maya.cmds` (or OpenMaya) *inside* the function and calls `bridge.run_main_thread(...)`. Never import Maya modules at file top level — `tools/` is loaded by the MCP worker thread, not Maya's main thread.
5. **Register the tool** in `tools/__init__.py` if it's in a new module.

---

## Workflow: writing an agent system prompt

The system prompt is doing real work — it's the difference between an agent that wrecks the scene on its third turn and one that actually ships. The template at `assets/agent_system_prompt.md` covers the universally important parts. Customize these per pipeline:

- **Selection discipline.** Many Maya commands operate on the active selection. The agent should always be explicit (`cmds.select(..., r=True)`) before any command that uses it, or pass objects directly as arguments where the command allows.
- **Undo chunking.** Multi-step operations should be wrapped: `cmds.undoInfo(openChunk=True, chunkName='...')` … `cmds.undoInfo(closeChunk=True)`. The agent should call this around any user-facing action so one Ctrl+Z reverts the whole thing.
- **Query before mutate.** Before setting an attribute, query it. Before deleting a node, check it exists. The Maya scene graph is mutable and the LLM is not.
- **Unit system + axis convention.** Defaults are centimeters and Y-up, but studios change this. The system prompt should state the assumption explicitly so the LLM doesn't silently produce 100×-scale geometry.
- **When to use OpenMaya.** Most agents shouldn't unless asked — `cmds` is fine. The system prompt should bias toward the simpler API.

See `references/agent-prompt-guide.md` for the full rubric and worked examples.

---

## What the user receives at the end

A directory like this (or just the changed files, if they were iterating):

```
their-project/
├── pyproject.toml
├── README.md                       # how to install + run + connect Claude Desktop
├── maya_mcp/
│   ├── __init__.py
│   ├── server.py                   # FastMCP app + start_server()
│   ├── bridge.py                   # main-thread marshalling
│   ├── userSetup.py                # Maya autoload
│   └── tools/
│       ├── __init__.py
│       ├── scene.py
│       └── mesh.py
├── claude_desktop_config.example.json
└── agent_system_prompt.md          # paste into Claude Project / system prompt
```

Tell the user:
1. How to install (copy module to `MAYA_MODULE_PATH`, or use a `.mod` file).
2. How to verify (start Maya, check Script Editor for `[maya-mcp] listening on http://localhost:PORT`).
3. How to connect Claude Desktop (the example config uses `mcp-remote` to bridge stdio↔http).
4. How to add the system prompt (Projects → Custom Instructions, or paste at the start of a conversation).

---

## Don't reinvent things the scaffold already solves

These show up repeatedly when people first build a Maya MCP. The scaffold handles them; don't write new code for them unless the user asks:

- **Thread marshalling** — `bridge.run_main_thread()` already does `executeInMainThreadWithResult` and propagates exceptions.
- **Port collision** — `server.py` picks the next free port if the default is taken, and writes the chosen port to `~/.maya-mcp/port` so the Claude Desktop config can read it.
- **Logging** — uses Python `logging` writing to Maya's Script Editor and a rotating file in the user's Maya prefs directory.
- **Graceful shutdown** — the daemon thread shuts down when Maya quits; no `atexit` needed.

---

## Reference files

- `references/architecture.md` — Threading model, transport choice, why `streamable-http`, the FastMCP-inside-Maya event loop.
- `references/api-selection.md` — When to use `maya.cmds` vs OpenMaya (`maya.api.OpenMaya`). Decision rules and worked examples.
- `references/agent-prompt-guide.md` — Rubric for Maya-agent system prompts, with full template walkthrough.

## Asset files (used directly in output)

- `assets/server_scaffold/` — Full project template to copy into the user's workspace.
- `assets/tool_template.py` — Single-tool boilerplate when adding one tool to an existing scaffold.
- `assets/agent_system_prompt.md` — System prompt template; customize for the user's pipeline.

## Scripts

- `scripts/parse_maya_doc.py` — Fetch a `help.autodesk.com` page and emit a structured tool spec (command name, flags, types, examples). Handles both Commands reference and OpenMaya `py_ref/`.
