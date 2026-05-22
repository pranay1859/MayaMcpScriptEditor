"""Rigging tools: joint hierarchy, skin clusters, and blendshapes.

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
def get_joint_hierarchy(root_joint: str) -> dict:
    """Walk the joint DAG tree from root_joint and return a nested structure.

    Args:
        root_joint: Name of the root joint to start from.

    Returns:
        Dict with keys:
          - ``root``: short name of the root joint.
          - ``joints``: list of dicts, one per joint (including root), each
            with ``name`` (short), ``long_name`` (full DAG path),
            ``orient`` ([x, y, z] in degrees), and ``children``
            (list of short names of direct joint children).

    Raises:
        ValueError: If the node doesn't exist or isn't a joint.

    Example:
        >>> get_joint_hierarchy('hip')
        {'root': 'hip', 'joints': [{'name': 'hip', 'long_name': '|hip', 'orient': [0.0, 0.0, 0.0], 'children': ['knee']}, ...]}

    Wraps maya.cmds.listRelatives, maya.cmds.getAttr.
    """
    def _do() -> dict:
        import maya.cmds as cmds

        if not cmds.objExists(root_joint):
            raise ValueError(f"No node named {root_joint!r}")
        if cmds.nodeType(root_joint) != "joint":
            raise ValueError(f"{root_joint!r} is not a joint (nodeType={cmds.nodeType(root_joint)!r})")

        # Gather all descendant joints (full paths), then prepend the root.
        descendants = cmds.listRelatives(
            root_joint, allDescendents=True, type="joint", fullPath=True
        ) or []

        # Resolve the root's full path — raise early if name is ambiguous.
        all_matches = cmds.ls(root_joint, long=True)
        if len(all_matches) > 1:
            raise ValueError(
                f"Ambiguous name {root_joint!r} matches {len(all_matches)} nodes: {all_matches}. "
                "Use a long DAG path to disambiguate."
            )
        root_long = all_matches[0]

        all_long_names = [root_long] + list(descendants)

        joints = []
        for long_name in all_long_names:
            short_name = long_name.split("|")[-1]
            orient_x = cmds.getAttr(long_name + ".jointOrientX")
            orient_y = cmds.getAttr(long_name + ".jointOrientY")
            orient_z = cmds.getAttr(long_name + ".jointOrientZ")
            direct_children = cmds.listRelatives(
                long_name, children=True, type="joint", fullPath=True
            ) or []
            children_short = [c.split("|")[-1] for c in direct_children]
            joints.append({
                "name": short_name,
                "long_name": long_name,
                "orient": [orient_x, orient_y, orient_z],
                "children": children_short,
            })

        return {"root": root_long.split("|")[-1], "joints": joints}

    return run_main_thread(_do)


@mcp.tool()
def get_skin_cluster_info(mesh: str) -> dict:
    """Find the skinCluster node on a mesh and list all bound joints.

    Args:
        mesh: Mesh transform or shape name.

    Returns:
        Dict with keys:
          - ``mesh``: the input mesh name.
          - ``skin_cluster``: name of the skinCluster node.
          - ``joint_count``: number of influencing joints.
          - ``joints``: list of joint names.

    Raises:
        ValueError: If the mesh doesn't exist or has no skin cluster.

    Example:
        >>> get_skin_cluster_info('pSphere1')
        {'mesh': 'pSphere1', 'skin_cluster': 'skinCluster1', 'joint_count': 3, 'joints': ['hip', 'knee', 'ankle']}

    Wraps maya.cmds.listHistory, maya.cmds.skinCluster.
    """
    def _do() -> dict:
        import maya.cmds as cmds

        if not cmds.objExists(mesh):
            raise ValueError(f"No node named {mesh!r}")

        skin_clusters = cmds.listHistory(mesh, type="skinCluster") or []
        if not skin_clusters:
            raise ValueError(f"No skinCluster found on {mesh!r}")

        # Take the first skinCluster if multiple exist.
        sc = skin_clusters[0]

        influences = cmds.skinCluster(sc, query=True, influence=True) or []
        joint_list = list(influences)

        return {
            "mesh": mesh,
            "skin_cluster": sc,
            "joint_count": len(joint_list),
            "joints": joint_list,
        }

    return run_main_thread(_do)


@mcp.tool()
def get_blendshape_targets(mesh: str) -> dict:
    """Find all blendShape deformers on a mesh and list their targets and weights.

    Args:
        mesh: Mesh transform or shape name.

    Returns:
        Dict with keys:
          - ``mesh``: the input mesh name.
          - ``blendshapes``: list of dicts, one per blendShape node, each
            with ``name`` (node name), ``targets`` (list of target names),
            and ``weights`` (list of current weight floats).

    Raises:
        ValueError: If the mesh doesn't exist.

    Example:
        >>> get_blendshape_targets('pSphere1')
        {'mesh': 'pSphere1', 'blendshapes': [{'name': 'blendShape1', 'targets': ['smile', 'frown'], 'weights': [0.0, 0.0]}]}

    Wraps maya.cmds.listHistory, maya.cmds.listAttr, maya.cmds.blendShape.
    Note: target names are retrieved via ``cmds.listAttr(bs + '.w', multi=True)``
    because the ``blendShape -query -target`` flag is edit-only in Maya's API.
    """
    def _do() -> dict:
        import maya.cmds as cmds

        if not cmds.objExists(mesh):
            raise ValueError(f"No node named {mesh!r}")

        bs_nodes = cmds.listHistory(mesh, type="blendShape") or []

        blendshapes = []
        for bs in bs_nodes:
            # aliasAttr returns interleaved [alias, weight_attr, ...] pairs.
            # Pull even-indexed elements to get only the human-readable aliases.
            alias_pairs = cmds.aliasAttr(bs, query=True) or []
            target_names = alias_pairs[0::2]
            weights_raw = cmds.blendShape(bs, query=True, weight=True)
            weights = list(weights_raw) if weights_raw else []
            blendshapes.append({
                "name": bs,
                "targets": target_names,
                "weights": weights,
            })

        return {"mesh": mesh, "blendshapes": blendshapes}

    return run_main_thread(_do)
