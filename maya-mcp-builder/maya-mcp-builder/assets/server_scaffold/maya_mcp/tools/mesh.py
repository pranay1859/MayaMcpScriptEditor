"""Mesh-level tools. Demonstrates the cmds + OpenMaya mix.

The bulk-data read tools use OpenMaya (`MFnMesh.getPoints`) because
calling cmds.xform per vertex on a 10k-vertex mesh is unusably slow.
The mutating tool here uses cmds for simplicity and so undo works
without explicit MDGModifier handling.
"""
from __future__ import annotations

from typing import Literal

from maya_mcp.bridge import run_main_thread
from maya_mcp.server import mcp


@mcp.tool()
def get_mesh_summary(mesh: str) -> dict:
    """Return vertex/edge/face counts and a bounding box for a mesh.

    Args:
        mesh: Mesh transform or shape name.

    Returns:
        Dict with keys: ``vertices``, ``edges``, ``faces``, ``bbox_min``,
        ``bbox_max`` (world-space).

    Raises:
        ValueError: If the node doesn't exist or isn't a mesh.

    Example:
        >>> get_mesh_summary('pCube1')
        {'vertices': 8, 'edges': 12, 'faces': 6, 'bbox_min': [-0.5, -0.5, -0.5], 'bbox_max': [0.5, 0.5, 0.5]}

    Uses OpenMaya for the counts because it's already a one-call read.
    """
    def _do() -> dict:
        import maya.cmds as cmds
        import maya.api.OpenMaya as om

        if not cmds.objExists(mesh):
            raise ValueError(f"No node named {mesh!r}")

        sel = om.MSelectionList()
        sel.add(mesh)
        try:
            dag = sel.getDagPath(0)
            dag.extendToShape()
        except RuntimeError as exc:
            raise ValueError(f"{mesh!r} is not a DAG node") from exc

        if not dag.hasFn(om.MFn.kMesh):
            raise ValueError(f"{mesh!r} is not a mesh")

        fn_mesh = om.MFnMesh(dag)
        bbox = cmds.exactWorldBoundingBox(mesh)
        return {
            "vertices": fn_mesh.numVertices,
            "edges": fn_mesh.numEdges,
            "faces": fn_mesh.numPolygons,
            "bbox_min": [bbox[0], bbox[1], bbox[2]],
            "bbox_max": [bbox[3], bbox[4], bbox[5]],
        }

    return run_main_thread(_do)


@mcp.tool()
def extrude_faces(
    mesh: str,
    face_indices: list[int],
    offset: float = 0.5,
    direction: Literal["normal", "x", "y", "z"] = "normal",
) -> str:
    """Extrude faces of a mesh by an offset along their normals or an axis.

    Args:
        mesh: Mesh transform name.
        face_indices: List of face indices to extrude (e.g. ``[0, 1, 4]``).
        offset: Extrusion distance in scene units.
        direction: 'normal' uses each face's outward normal; 'x'/'y'/'z'
            extrude along the world axis.

    Returns:
        The node name of the resulting extrusion operator.

    Raises:
        ValueError: If the mesh doesn't exist or no faces are specified.

    Example:
        >>> extrude_faces('pCube1', [0, 1], offset=0.5)
        'polyExtrudeFace1'

    Wraps maya.cmds.polyExtrudeFacet, wrapped in an undo chunk so the
    user can Ctrl+Z the whole operation.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/polyExtrudeFacet.html
    """
    if not face_indices:
        raise ValueError("face_indices is empty")

    def _do() -> str:
        import maya.cmds as cmds

        if not cmds.objExists(mesh):
            raise ValueError(f"No node named {mesh!r}")

        face_components = [f"{mesh}.f[{i}]" for i in face_indices]

        cmds.undoInfo(openChunk=True, chunkName="mcp:extrude_faces")
        try:
            if direction == "normal":
                node = cmds.polyExtrudeFacet(
                    *face_components,
                    localTranslateZ=offset,
                    constructionHistory=True,
                )
            else:
                tx, ty, tz = 0.0, 0.0, 0.0
                if direction == "x":
                    tx = offset
                elif direction == "y":
                    ty = offset
                elif direction == "z":
                    tz = offset
                node = cmds.polyExtrudeFacet(
                    *face_components,
                    translateX=tx,
                    translateY=ty,
                    translateZ=tz,
                    constructionHistory=True,
                )
        finally:
            cmds.undoInfo(closeChunk=True)
        return node[0]

    return run_main_thread(_do)
