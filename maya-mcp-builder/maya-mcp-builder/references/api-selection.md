# Choosing the API: `maya.cmds` vs OpenMaya

Maya exposes two major Python surfaces. Pick the right one and the tool will be 5 lines; pick the wrong one and it's 50 lines or 100x slower. This guide is the decision rule.

## Quick rule

**Default to `maya.cmds`.** Reach for OpenMaya only when one of these applies:

1. **Mesh data manipulation.** Reading or writing per-vertex/per-face data, normals, UVs, weights. `cmds` makes you query one value at a time; `MFnMesh` gives you bulk arrays.
2. **Custom DG/DAG nodes.** Writing a plugin node, evaluator, deformer. `cmds` can't author these.
3. **High-iteration scene operations.** Looping over 10,000+ objects/components. `cmds` round-trips through MEL on every call; OpenMaya stays in C++ land.
4. **The doc says "this is slow"** or "use the API instead." Autodesk flags this on a handful of commands (`xform` with many objects, per-vertex `polyMoveVertex`, etc.).
5. **You need an `MObject` handle.** Some APIs only consume MObjects (e.g. iterator construction, dependency-graph traversal).

For everything else — scene management, creating primitives, setting attributes, file I/O, selection, rendering setup — `cmds` is more concise and the LLM understands it better because the documentation is more agent-friendly.

## What the two APIs look like

### `maya.cmds` — procedural, MEL-mirror

The Python Commands reference (`help.autodesk.com/.../CommandsPython/`) documents every MEL command as a Python function. Flags become keyword arguments. Most commands have three modes — create, query, edit — controlled by the `query` and `edit` keywords.

```python
import maya.cmds as cmds

# Create
cube = cmds.polyCube(name='myCube', width=2)[0]    # returns ['myCube', 'polyCube1']
cmds.setAttr(f'{cube}.translateY', 5)

# Query
ty = cmds.getAttr(f'{cube}.translateY')

# Edit (with the edit flag)
cmds.polyCube('myCube', edit=True, width=3)
```

Strengths:
- One-liners for most scene work.
- Every command has a doc page with examples — easy for Claude to learn from.
- Undo works out of the box on every command.

Weaknesses:
- Slow for bulk operations (each call hops MEL→Python→C++).
- String-based object references — typos at runtime, not at edit time.
- Some operations are missing entirely (e.g. you can't author a custom node).

### OpenMaya (`maya.api.OpenMaya`) — OOP, C++-backed

The Python API reference (`help.autodesk.com/.../py_ref/`) documents the OOP API. Objects (`MFnMesh`, `MDagPath`, `MFnDependencyNode`, `MItMeshVertex`) wrap Maya's C++ classes.

```python
import maya.api.OpenMaya as om

# Get a handle to the mesh shape
sel = om.MSelectionList()
sel.add('myCube')
dag = sel.getDagPath(0)
dag.extendToShape()

fn_mesh = om.MFnMesh(dag)

# Read all vertex positions in one call (returns MPointArray)
points = fn_mesh.getPoints(om.MSpace.kWorld)

# Move every vertex up by 1 unit, then write back
for i in range(len(points)):
    points[i].y += 1.0
fn_mesh.setPoints(points, om.MSpace.kWorld)
```

Strengths:
- Bulk data ops are 10–100× faster than the `cmds` equivalent.
- Type-safe handles instead of name strings (an `MObject` survives renames).
- Required for plugin authoring (`om.MPxNode`, etc.).

Weaknesses:
- Verbose; more setup per operation.
- Undo is not automatic — for editing commands you must wrap in an `MDGModifier` and call `doIt()`, or write a `MPxCommand` plugin.
- The docs are class-reference style — less LLM-friendly than the Commands ref.

> **Use the `maya.api.OpenMaya` module, not the old `maya.OpenMaya`.** The `api.` version is the Python 2.0 API — better array handling, proper exceptions, and is the one all current docs reference.

## Decision examples

| Task | Pick | Why |
|---|---|---|
| Create a cube | `cmds.polyCube` | Trivial scene op. |
| Set translateY on 1 object | `cmds.setAttr` | One-liner. |
| Set translateY on 10,000 objects from a Python list | OpenMaya `MFnDagNode` + `MPlug` | `cmds` will take seconds; OpenMaya is milliseconds. |
| Read vertex positions of a mesh for export | OpenMaya `MFnMesh.getPoints` | Bulk array; `cmds.xform` per vertex is unusable. |
| Save the scene | `cmds.file(save=True)` | OpenMaya has no equivalent. |
| List all cameras | `cmds.ls(type='camera')` | OpenMaya needs an `MItDag` iterator filtered by `kCamera`. Don't. |
| Write a deformer | OpenMaya `MPxDeformerNode` | `cmds` literally cannot. |
| Compute mesh face normals | OpenMaya `MFnMesh.getNormals` | `cmds.polyInfo(faceNormals=True)` returns a list of strings you have to parse. |
| Set a node's name | `cmds.rename` | `cmds` handles namespace + uniqueness rules; OpenMaya makes you handle them yourself. |
| Bind a skinCluster to a mesh | `cmds.skinCluster` | The `cmds` command does substantial setup that OpenMaya wouldn't replicate. |
| Iterate every vertex of every mesh in the scene | OpenMaya `MItDag` + `MItMeshVertex` | Two-level loop in `cmds` is dog-slow. |
| Get the type of a selected node | `cmds.objectType` | OpenMaya: `MFnDependencyNode(obj).typeName` — works but no upside. |

## Tools that mix both

A common pattern: a tool's outer shell is `cmds` (because it deals with names and selection) and its inner hot loop is OpenMaya. That's fine; do it when you need it. Example:

```python
@mcp.tool()
def mesh_average_position(mesh_name: str) -> dict:
    """Return the average world-space position of all vertices in a mesh."""
    def _do():
        import maya.cmds as cmds
        import maya.api.OpenMaya as om

        if not cmds.objExists(mesh_name):
            raise ValueError(f"No node named {mesh_name!r} in the scene")

        sel = om.MSelectionList()
        sel.add(mesh_name)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)
        pts = fn.getPoints(om.MSpace.kWorld)
        n = len(pts)
        if n == 0:
            return {"x": 0.0, "y": 0.0, "z": 0.0, "count": 0}
        sx = sum(p.x for p in pts)
        sy = sum(p.y for p in pts)
        sz = sum(p.z for p in pts)
        return {"x": sx / n, "y": sy / n, "z": sz / n, "count": n}

    return bridge.run_main_thread(_do)
```

`cmds.objExists` for the friendly existence check, OpenMaya for the bulk math. The tool is faster and more correct than a pure-`cmds` version.

## Undo with OpenMaya

If a tool *mutates* the scene through OpenMaya, undo doesn't happen for free. The two practical options:

1. **Wrap the whole tool body in an undo chunk.** This makes the user's Ctrl+Z roll back everything inside, even if Maya didn't record granular operations.
   ```python
   def _do():
       import maya.cmds as cmds
       cmds.undoInfo(openChunk=True, chunkName='mcp-tool: my_op')
       try:
           ...mutate via OpenMaya...
       finally:
           cmds.undoInfo(closeChunk=True)
   ```
2. **Use an `MDGModifier`.** Build the changes as a modifier and call `.doIt()`. The modifier integrates with Maya's undo queue properly. Heavier but correct.

For most agent-facing tools, option 1 is sufficient.

## Doc-link discipline

When the tool wraps a specific command, put the doc link in the docstring:

```python
@mcp.tool()
def poly_extrude_face(...):
    """Extrude polygon faces along their normals.

    Wraps maya.cmds.polyExtrudeFacet.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/polyExtrudeFacet.html
    """
```

This helps a human reviewer audit the wrapping against the source, and it helps the agent (it can fetch the doc when behavior is ambiguous).
