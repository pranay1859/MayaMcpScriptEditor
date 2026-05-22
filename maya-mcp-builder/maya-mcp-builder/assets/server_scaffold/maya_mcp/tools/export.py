"""Export and publish tools: freeze transforms, FBX, and Alembic export.

Every tool body imports Maya modules *inside* the inner ``_do`` function
and routes execution through ``bridge.run_main_thread``. Top-level
imports of ``maya.*`` are wrong here — this file is imported by the
FastMCP worker thread before Maya guarantees safe access.

See ``references/architecture.md`` in the maya-mcp-builder skill.
"""
from __future__ import annotations

from maya_mcp.bridge import run_main_thread
from maya_mcp.server import mcp


@mcp.tool()
def freeze_and_clean(objects: list[str]) -> dict:
    """Freeze transforms and delete construction history on a list of objects.

    Args:
        objects: List of Maya transform node names to process.

    Returns:
        Dict with keys:
            - ``frozen``: list of node names that were successfully frozen.
            - ``skipped``: list of dicts ``{"node": str, "reason": str}`` for
              objects that were skipped.
            - ``history_nodes_deleted``: total count of history nodes removed.

    Raises:
        ValueError: If any requested objects do not exist in the scene.

    Example:
        >>> freeze_and_clean(['pCube1', 'pSphere1'])
        {'frozen': ['pCube1'], 'skipped': [], 'history_nodes_deleted': 3}
    """
    def _do() -> dict:
        import maya.cmds as cmds

        # Validate all objects exist before touching anything.
        missing = [obj for obj in objects if not cmds.objExists(obj)]
        if missing:
            raise ValueError(f"Objects not found in scene: {missing}")

        # Safety check: skip objects with skin clusters.
        safe: list[str] = []
        skipped: list[dict] = []
        for obj in objects:
            skin_clusters = cmds.listHistory(obj, type="skinCluster") or []
            if skin_clusters:
                skipped.append({"node": obj, "reason": "has skin cluster"})
            else:
                safe.append(obj)

        # Count history nodes before deletion (exclude the object itself).
        history_count = 0
        for obj in safe:
            history = cmds.listHistory(obj) or []
            # listHistory includes the object's own shape/transform; subtract
            # the object itself to count only construction history nodes.
            history_count += max(0, len(history) - 1)

        frozen: list[str] = []

        cmds.undoInfo(openChunk=True, chunkName="mcp:freeze_and_clean")
        try:
            for obj in safe:
                cmds.makeIdentity(
                    obj,
                    apply=True,
                    translate=True,
                    rotate=True,
                    scale=True,
                    normal=False,
                )
                cmds.delete(obj, constructionHistory=True)
                frozen.append(obj)
        finally:
            cmds.undoInfo(closeChunk=True)

        return {
            "frozen": frozen,
            "skipped": skipped,
            "history_nodes_deleted": history_count,
        }

    return run_main_thread(_do)


@mcp.tool()
def export_selection_fbx(file_path: str, objects: list[str]) -> str:
    """Export a list of objects to FBX format.

    Args:
        file_path: Destination file path (absolute or relative). The
            directory is created if it does not exist.
        objects: List of Maya node names to export.

    Returns:
        The absolute path that was written.

    Raises:
        ValueError: If any objects are missing or the FBX plugin is
            unavailable.

    Example:
        >>> export_selection_fbx('/tmp/hero.fbx', ['pCube1'])
        '/tmp/hero.fbx'
    """
    def _do() -> str:
        import os
        import maya.cmds as cmds

        # Validate objects exist.
        missing = [obj for obj in objects if not cmds.objExists(obj)]
        if missing:
            raise ValueError(f"Objects not found in scene: {missing}")

        # Load FBX plugin.
        try:
            cmds.loadPlugin("fbxmaya", quiet=True)
        except RuntimeError:
            raise ValueError("FBX plugin not available")

        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            raise ValueError("FBX plugin not available")

        abs_path = os.path.abspath(file_path)
        dir_path = os.path.dirname(abs_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        cmds.undoInfo(openChunk=True, chunkName="mcp:export_selection_fbx")
        try:
            cmds.select(objects, replace=True)
            cmds.file(
                abs_path,
                force=True,
                options="v=0",
                type="FBX export",
                preserveReferences=True,
                exportSelected=True,
            )
        finally:
            cmds.undoInfo(closeChunk=True)

        return abs_path

    return run_main_thread(_do)


@mcp.tool()
def export_alembic(
    file_path: str,
    objects: list[str],
    frame_range: list[int] | None = None,
    uv_write: bool = True,
) -> str:
    """Export objects to Alembic (.abc) format.

    Args:
        file_path: Destination file path (absolute or relative). The
            directory is created if it does not exist.
        objects: List of Maya node names to export as roots.
        frame_range: Optional ``[start, end]`` frame range. If omitted,
            exports the current frame only.
        uv_write: If True, includes UV data in the export. Default True.

    Returns:
        The absolute path that was written.

    Raises:
        ValueError: If any objects are missing or the AbcExport plugin is
            unavailable.

    Example:
        >>> export_alembic('/tmp/anim.abc', ['pCube1'], frame_range=[1, 24])
        '/tmp/anim.abc'
    """
    def _do() -> str:
        import os
        import maya.cmds as cmds

        # Validate objects exist.
        missing = [obj for obj in objects if not cmds.objExists(obj)]
        if missing:
            raise ValueError(f"Objects not found in scene: {missing}")

        # Load AbcExport plugin.
        try:
            cmds.loadPlugin("AbcExport", quiet=True)
        except RuntimeError:
            raise ValueError("AbcExport plugin not available")

        if not cmds.pluginInfo("AbcExport", query=True, loaded=True):
            raise ValueError("AbcExport plugin not available")

        abs_path = os.path.abspath(file_path)
        dir_path = os.path.dirname(abs_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        # Build the -j argument string.
        tokens: list[str] = []

        if frame_range is not None:
            tokens.append(f"-frameRange {frame_range[0]} {frame_range[1]}")

        tokens.append("-dataFormat ogawa")

        if uv_write:
            tokens.append("-uvWrite")

        tokens.append("-worldSpace")

        for obj in objects:
            tokens.append(f'-root "{obj}"')

        # Normalize to forward slashes: AbcExport's MEL parser treats backslashes as escapes.
        fwd_path = abs_path.replace("\\", "/")
        tokens.append(f'-file "{fwd_path}"')

        j_arg = " ".join(tokens)
        cmds.AbcExport(j=j_arg)

        return abs_path

    return run_main_thread(_do)
