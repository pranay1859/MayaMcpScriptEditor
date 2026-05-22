"""Technical Director (TD) tools for Maya asset management.

These tools are designed for diagnosing and fixing scene issues — the kind
of work a Senior TD does before an asset ships: naming audits, reference
checks, history cleanup, DG debugging.

All tools follow the scaffold rules:
  - Maya imports go INSIDE the inner ``_do()`` function.
  - All Maya work routes through ``bridge.run_main_thread(_do)``.
  - Mutating tools open/close an undo chunk.
"""
from __future__ import annotations

import re
from typing import Literal

from maya_mcp.bridge import run_main_thread
from maya_mcp.server import mcp


# ---------------------------------------------------------------------------
# check_scene_health
# ---------------------------------------------------------------------------

@mcp.tool()
def check_scene_health() -> dict:
    """Scan the current Maya scene for common asset-management issues.

    Checks performed:
      - Broken or offline file references
      - Missing file texture paths (file node points to non-existent file)
      - Nodes with default Maya names (pCube1, mesh1, joint1, etc.)
      - Duplicate short names in the DAG
      - Transforms with multiple shape children
      - Transforms with non-identity translate/rotate/scale (unfrozen)
      - Mesh shapes with construction history (build history not deleted)

    Returns:
        Dict with keys ``errors``, ``warnings``, ``info``, and ``summary``.
        Each list contains dicts with ``category``, ``node``, and ``detail``.

    Example:
        >>> check_scene_health()
        {
          "errors": [{"category": "broken_reference", "node": "char_rig.ma", "detail": "Reference offline"}],
          "warnings": [{"category": "default_name", "node": "pCube1", "detail": "Default Maya name"}],
          "info": [],
          "summary": "1 error, 1 warning, 0 info"
        }
    """
    def _do() -> dict:
        import os
        import maya.cmds as cmds

        errors: list[dict] = []
        warnings: list[dict] = []
        info: list[dict] = []

        def _err(category: str, node: str, detail: str) -> None:
            errors.append({"category": category, "node": node, "detail": detail})

        def _warn(category: str, node: str, detail: str) -> None:
            warnings.append({"category": category, "node": node, "detail": detail})

        def _info(category: str, node: str, detail: str) -> None:
            info.append({"category": category, "node": node, "detail": detail})

        # -- File references ------------------------------------------------
        refs = cmds.file(q=True, reference=True) or []
        for ref in refs:
            try:
                is_loaded = cmds.referenceQuery(ref, isLoaded=True)
                if not is_loaded:
                    _err("offline_reference", ref, "Reference is not loaded (offline)")
                else:
                    ref_path = cmds.referenceQuery(ref, filename=True, withoutCopyNumber=True)
                    if not os.path.exists(ref_path):
                        _err("broken_reference", ref, f"Referenced file not found: {ref_path}")
            except RuntimeError:
                _err("broken_reference", ref, "Could not query reference — may be invalid")

        # -- Missing file textures ------------------------------------------
        file_nodes = cmds.ls(type="file") or []
        for fn in file_nodes:
            tex_path = cmds.getAttr(f"{fn}.fileTextureName") or ""
            if tex_path and not os.path.exists(tex_path):
                _err("missing_texture", fn, f"Texture file not found: {tex_path}")
            elif not tex_path:
                _warn("empty_texture", fn, "file node has no texture path set")

        # -- Default Maya names --------------------------------------------
        _default_name_re = re.compile(
            r"^(pCube|pSphere|pCylinder|pCone|pPlane|pTorus|pDisc|"
            r"mesh|nurbsSurface|nurbsCurve|subdiv|joint|"
            r"camera|spotLight|pointLight|directionalLight|areaLight|"
            r"ambientLight|group|locator|ikHandle|effector|cluster|"
            r"lattice|ffd|wrap|blendShape|skinCluster|"
            r"transform|lambert|blinn|phong|aiStandardSurface)\d+$"
        )
        dag_nodes = cmds.ls(dag=True, long=False) or []
        for node in dag_nodes:
            if _default_name_re.match(node):
                _warn("default_name", node, "Node has a default Maya-generated name")

        # -- Duplicate short names -----------------------------------------
        from collections import Counter
        name_counts = Counter(dag_nodes)
        for name, count in name_counts.items():
            if count > 1:
                _err("duplicate_name", name, f"Short name appears {count} times — use long paths to disambiguate")

        # -- Multiple shapes under one transform ---------------------------
        transforms = cmds.ls(type="transform", long=True) or []
        for xf in transforms:
            shapes = cmds.listRelatives(xf, shapes=True, fullPath=True) or []
            if len(shapes) > 1:
                _warn(
                    "multiple_shapes",
                    xf.split("|")[-1],
                    f"Transform has {len(shapes)} shape children — may cause export issues",
                )

        # -- Unfrozen transforms (non-identity) ----------------------------
        _tol = 1e-4
        for xf in transforms:
            # Skip the world/root nodes Maya creates
            short = xf.split("|")[-1]
            if short in ("persp", "top", "front", "side", "perspShape",
                         "topShape", "frontShape", "sideShape"):
                continue
            try:
                t = cmds.xform(xf, q=True, translation=True, worldSpace=False) or [0, 0, 0]
                r = cmds.xform(xf, q=True, rotation=True, worldSpace=False) or [0, 0, 0]
                s = cmds.xform(xf, q=True, scale=True, worldSpace=False) or [1, 1, 1]
                if (abs(t[0]) > _tol or abs(t[1]) > _tol or abs(t[2]) > _tol or
                        abs(r[0]) > _tol or abs(r[1]) > _tol or abs(r[2]) > _tol or
                        abs(s[0] - 1) > _tol or abs(s[1] - 1) > _tol or abs(s[2] - 1) > _tol):
                    _info("unfrozen_transform", short,
                          f"t={[round(v,3) for v in t]} r={[round(v,3) for v in r]} s={[round(v,3) for v in s]}")
            except RuntimeError:
                pass

        # -- Mesh history --------------------------------------------------
        mesh_shapes = cmds.ls(type="mesh", long=True) or []
        for ms in mesh_shapes:
            history = cmds.listHistory(ms, pruneDagObjects=True) or []
            # Filter out the shape itself and its skin cluster (expected history)
            non_trivial = [
                h for h in history
                if h != ms and cmds.nodeType(h) not in ("skinCluster", "tweak", "groupParts", "groupId")
            ]
            if non_trivial:
                short = ms.split("|")[-1]
                _info("mesh_history", short,
                      f"Mesh has {len(non_trivial)} history node(s): {', '.join(non_trivial[:3])}"
                      + (" ..." if len(non_trivial) > 3 else ""))

        n_e, n_w, n_i = len(errors), len(warnings), len(info)
        summary = f"{n_e} error{'s' if n_e != 1 else ''}, {n_w} warning{'s' if n_w != 1 else ''}, {n_i} info"
        return {"errors": errors, "warnings": warnings, "info": info, "summary": summary}

    return run_main_thread(_do)


# ---------------------------------------------------------------------------
# run_python_snippet
# ---------------------------------------------------------------------------

@mcp.tool()
def run_python_snippet(code: str, description: str) -> dict:
    """Execute a Python snippet in Maya's main thread and return the result.

    The ``description`` parameter is **required** — you must state in plain
    English what this code does before calling the tool. This is a safety
    guardrail: never call this without a clear human-readable description.

    Args:
        code: Valid Python source to execute inside Maya. Has access to the
            full Maya Python environment. ``exec()`` semantics — last
            expression value is NOT automatically returned; assign to a
            variable named ``_result`` to capture a return value.
        description: Plain-English explanation of what the code does and
            why. Required. Example: "Delete all intermediate objects on the
            hero mesh that have no connections."

    Returns:
        Dict with keys:
          ``stdout``  — anything printed to stdout during execution.
          ``result``  — string repr of ``_result`` if set, else empty string.
          ``error``   — exception message if execution failed, else null.
          ``description`` — echoed back for audit trail.

    Raises:
        ValueError: If ``description`` is empty (guardrail).

    Example:
        >>> run_python_snippet(
        ...     code="import maya.cmds as cmds\\n_result = cmds.ls(type='mesh')",
        ...     description="List all mesh nodes in the scene."
        ... )
        {"stdout": "", "result": "['pCubeShape1']", "error": null, "description": "..."}
    """
    if not description or not description.strip():
        raise ValueError(
            "description is required — state what the code does before running it."
        )

    def _do() -> dict:
        import sys
        import io

        old_stdout = sys.stdout
        sys.stdout = buf = io.StringIO()
        namespace: dict = {}
        error = None
        try:
            exec(code, namespace)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        finally:
            sys.stdout = old_stdout

        result_val = namespace.get("_result", "")
        return {
            "stdout": buf.getvalue(),
            "result": repr(result_val) if result_val != "" else "",
            "error": error,
            "description": description,
        }

    return run_main_thread(_do)


# ---------------------------------------------------------------------------
# get_node_connections
# ---------------------------------------------------------------------------

@mcp.tool()
def get_node_connections(
    node: str,
    direction: Literal["incoming", "outgoing", "both"] = "both",
    attribute: str | None = None,
) -> list[dict]:
    """List all DG connections to or from a node.

    Essential for debugging broken rigs, unexpected shader networks,
    expression dependencies, and any situation where you need to understand
    what a node is connected to before deleting or modifying it.

    Args:
        node: Node name (short or long path, e.g. ``pCube1`` or ``|group1|pCube1``).
        direction: Which connections to return.
            ``"incoming"``  — plugs feeding INTO this node.
            ``"outgoing"``  — plugs driven BY this node.
            ``"both"``      — all connections (default).
        attribute: Optional. If given, filter to connections on this specific
            attribute (e.g. ``"translate"``, ``"outMesh"``).

    Returns:
        List of connection dicts, each with:
          ``source``       — source plug (``"nodeA.attrX"``).
          ``destination``  — destination plug (``"nodeB.attrY"``).
          ``source_type``  — Maya node type of the source node.
          ``dest_type``    — Maya node type of the destination node.

    Raises:
        ValueError: If the node does not exist.

    Example:
        >>> get_node_connections("pCube1", direction="outgoing")
        [{"source": "pCube1.outMesh", "destination": "pCubeShape1.inMesh", ...}]

    Wraps maya.cmds.listConnections.
    """
    def _do() -> list[dict]:
        import maya.cmds as cmds

        if not cmds.objExists(node):
            raise ValueError(f"No node named {node!r} in the scene")

        target = f"{node}.{attribute}" if attribute else node

        results: list[dict] = []

        if direction in ("incoming", "both"):
            pairs = cmds.listConnections(
                target, source=True, destination=False,
                plugs=True, connections=True
            ) or []
            # listConnections with connections=True returns [dest, src, dest, src, ...]
            for i in range(0, len(pairs) - 1, 2):
                dst, src = pairs[i], pairs[i + 1]
                src_node = src.split(".")[0]
                dst_node = dst.split(".")[0]
                results.append({
                    "source": src,
                    "destination": dst,
                    "source_type": cmds.nodeType(src_node),
                    "dest_type": cmds.nodeType(dst_node),
                })

        if direction in ("outgoing", "both"):
            pairs = cmds.listConnections(
                target, source=False, destination=True,
                plugs=True, connections=True
            ) or []
            for i in range(0, len(pairs) - 1, 2):
                src, dst = pairs[i], pairs[i + 1]
                src_node = src.split(".")[0]
                dst_node = dst.split(".")[0]
                results.append({
                    "source": src,
                    "destination": dst,
                    "source_type": cmds.nodeType(src_node),
                    "dest_type": cmds.nodeType(dst_node),
                })

        return results

    return run_main_thread(_do)


# ---------------------------------------------------------------------------
# check_naming_convention
# ---------------------------------------------------------------------------

@mcp.tool()
def check_naming_convention(
    pattern: str,
    object_type: Literal["mesh", "joint", "transform", "any"] = "any",
) -> dict:
    """Validate node names against a regex pattern.

    Use this before reporting a scene as "clean" to confirm that all assets
    follow the studio naming convention. Violations are returned with their
    current names so the agent (or the user) can decide how to rename them.

    Args:
        pattern: A Python regex pattern that **valid** names must fully match.
            Examples:
              ``r"^[A-Z][a-zA-Z]+_(GEO|RIG|CTL|JNT|GRP|SHP|MAT)$"``
              ``r"^[a-z][a-zA-Z0-9]+_(geo|jnt|ctrl|grp)$"``
        object_type: Which node type to audit.
            ``"mesh"``       — mesh shape nodes only.
            ``"joint"``      — joint nodes only.
            ``"transform"``  — transform nodes only.
            ``"any"``        — all DAG nodes (default).

    Returns:
        Dict with:
          ``pattern``         — the regex used.
          ``object_type``     — the type filter used.
          ``total_checked``   — number of nodes inspected.
          ``compliant_count`` — number that matched the pattern.
          ``violations``      — list of ``{"node": str, "current_name": str}``
                                for non-matching nodes.

    Raises:
        ValueError: If ``pattern`` is not a valid regex.

    Example:
        >>> check_naming_convention(r"^[A-Za-z]+_(GEO|JNT|CTL)$", object_type="mesh")
        {"pattern": "...", "total_checked": 12, "compliant_count": 10,
         "violations": [{"node": "pCube1", "current_name": "pCube1"}, ...]}
    """
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern {pattern!r}: {exc}") from exc

    def _do() -> dict:
        import maya.cmds as cmds

        if object_type == "any":
            nodes = cmds.ls(dag=True, long=False) or []
        elif object_type == "mesh":
            nodes = cmds.ls(type="mesh", long=False) or []
        elif object_type == "joint":
            nodes = cmds.ls(type="joint", long=False) or []
        elif object_type == "transform":
            nodes = cmds.ls(type="transform", long=False) or []
        else:
            nodes = []

        violations = []
        for name in nodes:
            if not compiled.fullmatch(name):
                violations.append({"node": name, "current_name": name})

        return {
            "pattern": pattern,
            "object_type": object_type,
            "total_checked": len(nodes),
            "compliant_count": len(nodes) - len(violations),
            "violations": violations,
        }

    return run_main_thread(_do)
