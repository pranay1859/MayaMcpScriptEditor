"""Scene / namespace / reference management tools.

Every tool body imports Maya modules *inside* the inner ``_do`` function
and routes execution through ``bridge.run_main_thread``. Top-level
imports of ``maya.*`` are wrong here — this file is imported by the
FastMCP worker thread before Maya guarantees safe access.

See ``references/architecture.md`` in the maya-mcp-builder skill.
"""
from __future__ import annotations

from typing import Literal

from maya_mcp.bridge import run_main_thread
from maya_mcp.server import mcp


@mcp.tool()
def list_namespaces(include_root: bool = False) -> list[str]:
    """List all namespaces in the current scene.

    Args:
        include_root: If True, include the root namespace (":") and the
            built-in Maya namespaces "UI" and "shared". Default False.

    Returns:
        Sorted list of namespace strings.

    Example:
        >>> list_namespaces()
        ['character', 'character:rig', 'props']

    Wraps maya.cmds.namespaceInfo.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/namespaceInfo.html
    """
    def _do() -> list[str]:
        import maya.cmds as cmds  # INSIDE _do() ONLY

        result = cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True)
        if not result:
            return []
        namespaces: list[str] = list(result)
        if not include_root:
            namespaces = [
                ns for ns in namespaces
                if ns not in (":", "UI", "shared")
            ]
        return sorted(namespaces)

    return run_main_thread(_do)


@mcp.tool()
def query_references() -> list[dict]:
    """Return all file references in the current scene with their status.

    Returns:
        List of dicts, one per reference, with keys:
        - ``reference_node`` (str): The Maya reference node name.
        - ``file_path`` (str): Clean absolute file path without copy number.
        - ``namespace`` (str): The namespace the reference was brought in under.
          Empty string if unavailable (e.g. reference is unloaded or unnamed).
        - ``is_loaded`` (bool): Whether the reference is currently loaded.

    Example:
        >>> query_references()
        [{"reference_node": "characterRN", "file_path": "/assets/char.ma",
          "namespace": "character", "is_loaded": True}]

    Wraps maya.cmds.file and maya.cmds.referenceQuery.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/file.html
    """
    def _do() -> list[dict]:
        import maya.cmds as cmds  # INSIDE _do() ONLY

        ref_paths = cmds.file(query=True, reference=True) or []
        results: list[dict] = []
        for ref in ref_paths:
            try:
                clean_path = cmds.referenceQuery(
                    ref, filename=True, withoutCopyNumber=True
                )
                is_loaded = cmds.referenceQuery(ref, isLoaded=True)
                ref_node = cmds.referenceQuery(ref, referenceNode=True)
                try:
                    namespace = cmds.referenceQuery(ref, namespace=True)
                except RuntimeError:
                    namespace = ""
                results.append(
                    {
                        "reference_node": ref_node,
                        "file_path": clean_path,
                        "namespace": namespace,
                        "is_loaded": bool(is_loaded),
                    }
                )
            except RuntimeError:
                continue
        return results

    return run_main_thread(_do)


@mcp.tool()
def load_unload_reference(
    reference_node: str,
    action: Literal["load", "unload"],
) -> dict:
    """Load or unload a file reference by its reference node name.

    Args:
        reference_node: Name of the Maya reference node (e.g. ``characterRN``).
        action: ``"load"`` to load the reference, ``"unload"`` to unload it.

    Returns:
        Dict with keys:
        - ``reference_node`` (str): The reference node name.
        - ``action`` (str): The action that was performed.
        - ``is_loaded`` (bool): The new load state after the action.

    Raises:
        ValueError: If ``reference_node`` does not exist, is not a reference
            node, or ``action`` is not ``"load"`` or ``"unload"``.

    Example:
        >>> load_unload_reference("characterRN", "unload")
        {"reference_node": "characterRN", "action": "unload", "is_loaded": False}

    Wraps maya.cmds.file.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/file.html
    """
    if action not in ("load", "unload"):
        raise ValueError(
            f"action must be 'load' or 'unload', got {action!r}"
        )

    def _do() -> dict:
        import maya.cmds as cmds  # INSIDE _do() ONLY

        if not cmds.objExists(reference_node):
            raise ValueError(
                f"No node named {reference_node!r} in the scene"
            )
        node_type = cmds.nodeType(reference_node)
        if node_type != "reference":
            raise ValueError(
                f"Node {reference_node!r} is type {node_type!r}, not a reference node"
            )

        cmds.undoInfo(openChunk=True, chunkName="mcp:load_unload_reference")
        try:
            if action == "load":
                cmds.file(loadReference=reference_node)
            else:
                cmds.file(unloadReference=reference_node)
        finally:
            cmds.undoInfo(closeChunk=True)

        is_loaded = cmds.referenceQuery(reference_node, isLoaded=True)
        return {
            "reference_node": reference_node,
            "action": action,
            "is_loaded": bool(is_loaded),
        }

    return run_main_thread(_do)
