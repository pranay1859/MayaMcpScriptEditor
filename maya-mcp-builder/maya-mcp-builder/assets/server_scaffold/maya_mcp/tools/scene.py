"""Scene-level tools: queries, file ops, selection, simple primitives.

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
def list_scene_objects(
    object_type: Literal[
        "any", "mesh", "camera", "light", "transform", "material"
    ] = "any",
    long_names: bool = False,
) -> list[str]:
    """List objects in the current Maya scene, optionally filtered by type.

    Args:
        object_type: 'any' lists every DAG node; otherwise filters to
            meshes, cameras, lights, transforms, or materials.
        long_names: If True, returns full DAG paths like
            ``|group1|pCube1``. Default returns short names.

    Returns:
        A list of node name strings. Empty list if nothing matches.

    Example:
        >>> list_scene_objects(object_type='camera')
        ['persp', 'top', 'front', 'side']

    Wraps maya.cmds.ls.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/ls.html
    """
    def _do() -> list[str]:
        import maya.cmds as cmds

        kwargs: dict = {"long": long_names}
        if object_type == "any":
            kwargs["dag"] = True
        elif object_type == "material":
            # Materials live in the shading network, not the DAG.
            kwargs["materials"] = True
        else:
            kwargs["type"] = object_type
        result = cmds.ls(**kwargs)
        return list(result) if result else []

    return run_main_thread(_do)


@mcp.tool()
def get_attribute(node: str, attribute: str) -> object:
    """Read a single attribute from a node.

    Args:
        node: Node name (e.g. ``pCube1``).
        attribute: Attribute long or short name (e.g. ``translateY``, ``ty``).

    Returns:
        The attribute value. Numeric attrs return float/int; vector attrs
        return a list; string attrs return str.

    Raises:
        ValueError: If the node or attribute does not exist.

    Example:
        >>> get_attribute('pCube1', 'translateX')
        0.0

    Wraps maya.cmds.getAttr.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/getAttr.html
    """
    def _do():
        import maya.cmds as cmds

        if not cmds.objExists(node):
            raise ValueError(f"No node named {node!r} in the scene")
        plug = f"{node}.{attribute}"
        if not cmds.objExists(plug):
            raise ValueError(f"Node {node!r} has no attribute {attribute!r}")
        return cmds.getAttr(plug)

    return run_main_thread(_do)


@mcp.tool()
def set_attribute(node: str, attribute: str, value: float | int | str | bool) -> str:
    """Set a single attribute on a node, wrapped in an undo chunk.

    Args:
        node: Node name.
        attribute: Attribute long or short name.
        value: New value. Numeric attrs accept int/float; string attrs
            accept str; boolean attrs accept bool.

    Returns:
        Confirmation string ``"{node}.{attribute} = {value}"``.

    Raises:
        ValueError: If the node or attribute does not exist or is locked.

    Example:
        >>> set_attribute('pCube1', 'translateX', 5.0)
        'pCube1.translateX = 5.0'

    Wraps maya.cmds.setAttr.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/setAttr.html
    """
    def _do() -> str:
        import maya.cmds as cmds

        if not cmds.objExists(node):
            raise ValueError(f"No node named {node!r} in the scene")
        plug = f"{node}.{attribute}"
        if not cmds.objExists(plug):
            raise ValueError(f"Node {node!r} has no attribute {attribute!r}")
        if cmds.getAttr(plug, lock=True):
            raise ValueError(f"{plug} is locked")

        cmds.undoInfo(openChunk=True, chunkName=f"mcp:set_attribute {plug}")
        try:
            if isinstance(value, str):
                cmds.setAttr(plug, value, type="string")
            else:
                cmds.setAttr(plug, value)
        finally:
            cmds.undoInfo(closeChunk=True)
        return f"{plug} = {value}"

    return run_main_thread(_do)


@mcp.tool()
def create_primitive(
    shape: Literal["cube", "sphere", "cylinder", "cone", "plane", "torus"],
    name: str | None = None,
    size: float = 1.0,
) -> str:
    """Create a polygon primitive at the origin and return its transform name.

    Args:
        shape: Which primitive to create.
        name: Desired node name. If omitted, Maya assigns a default
            (``pCube1``, ``pSphere1``, etc.).
        size: Width/radius depending on shape. Defaults to 1 scene unit.

    Returns:
        The created transform's node name.

    Example:
        >>> create_primitive('cube', name='hero_box', size=2.0)
        'hero_box'

    Wraps maya.cmds.polyCube / polySphere / polyCylinder / polyCone /
    polyPlane / polyTorus depending on ``shape``.
    """
    creators = {
        "cube": ("polyCube", dict(width=1, height=1, depth=1)),
        "sphere": ("polySphere", dict(radius=0.5)),
        "cylinder": ("polyCylinder", dict(radius=0.5, height=1)),
        "cone": ("polyCone", dict(radius=0.5, height=1)),
        "plane": ("polyPlane", dict(width=1, height=1)),
        "torus": ("polyTorus", dict(radius=0.5, sectionRadius=0.15)),
    }

    def _do() -> str:
        import maya.cmds as cmds

        fn_name, base_kwargs = creators[shape]
        # Scale the size-relevant kwargs uniformly.
        kwargs = {k: v * size for k, v in base_kwargs.items()}
        if name:
            kwargs["name"] = name

        cmds.undoInfo(openChunk=True, chunkName=f"mcp:create {shape}")
        try:
            result = getattr(cmds, fn_name)(**kwargs)
        finally:
            cmds.undoInfo(closeChunk=True)
        # cmds.polyX returns [transform, constructionNode]
        return result[0]

    return run_main_thread(_do)


@mcp.tool()
def save_scene(path: str | None = None) -> str:
    """Save the current scene.

    Args:
        path: Optional new path. If omitted, saves to the current scene
            path (errors if the scene has never been saved).

    Returns:
        The absolute path the scene was saved to.

    Example:
        >>> save_scene('/project/scenes/hero.ma')
        '/project/scenes/hero.ma'

    Wraps maya.cmds.file.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/file.html
    """
    def _do() -> str:
        import maya.cmds as cmds

        if path:
            # Decide file type from extension.
            ext = path.lower().rsplit(".", 1)[-1]
            file_type = "mayaAscii" if ext == "ma" else "mayaBinary"
            cmds.file(rename=path)
            return cmds.file(save=True, type=file_type)
        return cmds.file(save=True)

    return run_main_thread(_do)
