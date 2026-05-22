"""Rendering and shading tools: material queries, assignment, render globals.

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
def list_materials_and_assignments() -> list[dict]:
    """List all non-default shaders and which objects they are assigned to.

    Returns:
        A list of dicts, one per material, each with keys:
        ``material`` (str), ``type`` (str), ``shading_group`` (str),
        ``assigned_to`` (list[str]).  ``assigned_to`` is empty when the
        shading group exists but has no geometry members.

    Example:
        >>> list_materials_and_assignments()
        [{'material': 'myShader', 'type': 'lambert',
          'shading_group': 'myShaderSG', 'assigned_to': ['pCube1']}]

    Wraps maya.cmds.ls, maya.cmds.listConnections, maya.cmds.sets.
    """
    def _do() -> list[dict]:
        import maya.cmds as cmds  # INSIDE _do(), never at module top

        _DEFAULT_SHADERS = {"lambert1", "particleCloud1", "shaderGlow1"}

        all_materials = cmds.ls(materials=True) or []
        result = []
        for mat in all_materials:
            if mat in _DEFAULT_SHADERS:
                continue
            mat_type = cmds.nodeType(mat)
            shading_groups = cmds.listConnections(mat, type="shadingEngine") or []
            if not shading_groups:
                result.append({
                    "material": mat,
                    "type": mat_type,
                    "shading_group": "",
                    "assigned_to": [],
                })
                continue
            seen: set[str] = set()
            unique_sgs: list[str] = []
            for sg in shading_groups:
                if sg not in seen:
                    seen.add(sg)
                    unique_sgs.append(sg)
            for sg in unique_sgs:
                members = cmds.sets(sg, query=True) or []
                result.append({
                    "material": mat,
                    "type": mat_type,
                    "shading_group": sg,
                    "assigned_to": list(members),
                })
        return result

    return run_main_thread(_do)


@mcp.tool()
def assign_material(objects: list[str], material: str) -> dict:
    """Assign an existing material to one or more objects.

    Args:
        objects: Non-empty list of object names to assign the material to.
        material: Name of an existing material node.

    Returns:
        Dict with keys ``material`` (str), ``shading_group`` (str),
        ``assigned_to`` (list[str]).

    Raises:
        ValueError: If ``objects`` is empty, any object does not exist,
            the material does not exist, or no shading group is found for
            the material.

    Example:
        >>> assign_material(['pCube1', 'pSphere1'], 'myShader')
        {'material': 'myShader', 'shading_group': 'myShaderSG',
         'assigned_to': ['pCube1', 'pSphere1']}

    Wraps maya.cmds.sets (with edit/forceElement), wrapped in an undo
    chunk so the user can Ctrl+Z.
    """
    if not objects:
        raise ValueError("objects list must not be empty")

    def _do() -> dict:
        import maya.cmds as cmds  # INSIDE _do(), never at module top

        for obj in objects:
            if not cmds.objExists(obj):
                raise ValueError(f"No object named {obj!r} in the scene")
        if not cmds.objExists(material):
            raise ValueError(f"No material named {material!r} in the scene")

        # Find the shading group — catch RuntimeError when .outColor doesn't exist.
        try:
            shading_groups = cmds.listConnections(
                material + ".outColor", type="shadingEngine"
            )
        except RuntimeError as exc:
            raise ValueError(
                f"Material {material!r} has no .outColor attribute — "
                f"is it a valid surface shader? ({exc})"
            ) from exc

        if not shading_groups:
            raise ValueError(
                f"Material {material!r} has no connected shading group. "
                "Create a shading group before assigning."
            )
        shading_group = shading_groups[0]

        cmds.undoInfo(openChunk=True, chunkName="mcp:assign_material")
        try:
            cmds.sets(objects, edit=True, forceElement=shading_group)
        finally:
            cmds.undoInfo(closeChunk=True)

        return {
            "material": material,
            "shading_group": shading_group,
            "assigned_to": list(objects),
        }

    return run_main_thread(_do)


@mcp.tool()
def get_render_settings() -> dict:
    """Return current render globals: renderer, resolution, frame range, output path.

    Returns:
        Dict with keys: ``renderer`` (str), ``width`` (int), ``height``
        (int), ``start_frame`` (float), ``end_frame`` (float),
        ``image_format`` (int), ``output_path`` (str).  Attributes that
        do not exist on the current Maya version return ``None``.

    Example:
        >>> get_render_settings()
        {'renderer': 'arnold', 'width': 1920, 'height': 1080,
         'start_frame': 1.0, 'end_frame': 100.0,
         'image_format': 8, 'output_path': '/project/images/'}

    Wraps maya.cmds.getAttr (defaultRenderGlobals, defaultResolution)
    and maya.cmds.workspace.
    """
    def _do() -> dict:
        import os
        import maya.cmds as cmds  # INSIDE _do(), never at module top

        def _getattr_safe(plug, default=None):
            try:
                return cmds.getAttr(plug)
            except Exception:  # noqa: BLE001
                return default

        renderer = _getattr_safe("defaultRenderGlobals.currentRenderer")
        width = _getattr_safe("defaultResolution.width")
        height = _getattr_safe("defaultResolution.height")
        start_frame = _getattr_safe("defaultRenderGlobals.startFrame")
        end_frame = _getattr_safe("defaultRenderGlobals.endFrame")
        image_format = _getattr_safe("defaultRenderGlobals.imageFormat")

        try:
            root_dir = cmds.workspace(query=True, rootDirectory=True) or ""
            output_path: str = os.path.join(root_dir, "images")
        except Exception:  # noqa: BLE001
            output_path = ""

        return {
            "renderer": renderer,
            "width": int(width) if width is not None else None,
            "height": int(height) if height is not None else None,
            "start_frame": float(start_frame) if start_frame is not None else None,
            "end_frame": float(end_frame) if end_frame is not None else None,
            "image_format": int(image_format) if image_format is not None else None,
            "output_path": output_path,
        }

    return run_main_thread(_do)
