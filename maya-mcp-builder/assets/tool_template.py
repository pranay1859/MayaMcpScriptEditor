"""Single-tool template.

Copy this to ``maya_mcp/tools/<your_module>.py``, edit the function,
then add ``from maya_mcp.tools import <your_module>`` to
``maya_mcp.server._load_tools``.

The patterns enforced here are NOT cosmetic — see ``references/architecture.md``:
  - Maya imports go INSIDE the function (not at top of file).
  - Maya work goes through ``run_main_thread`` (never call Maya directly
    from the worker thread).
  - Mutating tools open and close an undo chunk (so one Ctrl+Z reverts).
  - Existence/validity checks raise ``ValueError`` with a clear message;
    the MCP SDK turns these into actionable errors the LLM can recover from.
  - The docstring is the agent's primary interface — describe what the
    tool does, list the args, include at least one example call, link
    to the upstream Maya doc.
"""
from __future__ import annotations

# Use Literal / TypedDict / Annotated as needed for richer schemas.
# from typing import Literal

from maya_mcp.bridge import run_main_thread
from maya_mcp.server import mcp


@mcp.tool()
def my_tool_name(
    required_arg: str,
    optional_arg: float = 1.0,
    flag: bool = False,
) -> dict:
    """One-line summary of what the tool does (this line shows in tool lists).

    Longer description if needed. Mention behavior the schema can't capture:
    side effects, what the tool refuses to do, what it expects of scene state.

    Args:
        required_arg: What this is and what valid values look like.
        optional_arg: Same. Default behavior when omitted.
        flag: Same.

    Returns:
        What the agent gets back. Describe the shape of the dict (or
        whatever the return type is) so the LLM knows what to access.

    Raises:
        ValueError: When and why.

    Example:
        >>> my_tool_name('pCube1', optional_arg=2.0)
        {'name': 'pCube1', 'result': 2.0}

    Wraps maya.cmds.<command>.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/<command>.html
    """
    def _do() -> dict:
        # Imports go INSIDE the function. See architecture notes.
        import maya.cmds as cmds
        # import maya.api.OpenMaya as om   # only if you need OpenMaya

        # Validate inputs first. Raise ValueError with a clear message;
        # the MCP SDK surfaces these as recoverable errors.
        if not cmds.objExists(required_arg):
            raise ValueError(f"No node named {required_arg!r} in the scene")

        # Mutating? Open an undo chunk so one Ctrl+Z reverts the whole tool.
        cmds.undoInfo(openChunk=True, chunkName=f"mcp:my_tool_name {required_arg}")
        try:
            # ... do the Maya work here ...
            result_value = optional_arg
        finally:
            cmds.undoInfo(closeChunk=True)

        return {"name": required_arg, "result": result_value}

    return run_main_thread(_do)
