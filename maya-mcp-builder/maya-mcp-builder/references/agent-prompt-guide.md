# Writing a system prompt for a Maya agent

The system prompt is the contract between the LLM and the MCP server. If the prompt is weak, the agent makes scene-corrupting mistakes that look reasonable in the transcript. This guide is the rubric — use it when you customize `assets/agent_system_prompt.md` for a specific pipeline.

## What the prompt has to do

In rough order of importance:

1. **Stop the agent from destroying state by accident.** Maya scenes are mutable, edits persist, and a wrong `cmds.delete('*')` is unrecoverable mid-conversation. The prompt has to instill caution about destructive operations.
2. **Establish the selection model.** A huge fraction of Maya commands operate on the current selection. The agent must either pass objects explicitly or set selection explicitly — never assume.
3. **Establish the unit/axis/coordinate conventions.** Defaults differ by studio.
4. **Bias the API choice.** Cmds for most things, OpenMaya only when needed. Without this, the agent will start reaching for OpenMaya prematurely and produce harder-to-review code.
5. **Encourage query-before-mutate.** The cheapest debugging step is "did the thing I'm about to edit exist with the shape I expect?" The prompt should make that the default.
6. **Encode pipeline-specific knowledge** the LLM can't infer (naming conventions, asset paths, custom node types, hand-rolled commands).

The template in `assets/agent_system_prompt.md` covers items 1–5 generically. Item 6 is the customization layer.

## What the prompt should not do

- **Don't list every available tool in the prompt.** The MCP server already exposes tool schemas. Duplicating them in prose wastes tokens and goes stale.
- **Don't write the prompt as a wall of `ALWAYS` and `NEVER`.** The model has good instincts; explain *why* and it'll generalize. Reserve hard rules for the few things that are genuinely non-negotiable (e.g. "never call `cmds.file(force=True, new=True)` without first asking").
- **Don't try to teach Maya from scratch.** The model knows Maya. The prompt's job is to surface pipeline-specific facts and operational discipline, not to be a textbook.

## The customization checklist

Walk through these with the user when generating the prompt:

| Field | Question | Example answers |
|---|---|---|
| **Maya version** | Which version is this running against? | `2026`, `2024`, etc. |
| **Linear unit** | cm, m, in, ft? | Default `cm`. Many studios use `cm` for CG, some use `m` for archviz. |
| **Angular unit** | degrees or radians? | Default `degrees`. |
| **Up axis** | Y or Z? | Default `Y`. DCC pipelines often Y; engineering/sim often Z. |
| **Time unit / FPS** | 24, 25, 30, 60? | `24` for film, `30`/`60` for games/realtime. |
| **Naming convention** | How are nodes named? | `<asset>_<part>_<type>` e.g. `hero_arm_GEO` |
| **Workspace layout** | Where do scenes / textures / caches live? | Conventional Maya workspace, or custom root |
| **Forbidden ops** | Anything the agent must never do? | `cmds.file(new=True, force=True)`, full-scene deletes, plugin loads |
| **Required ops** | Anything every save/export needs? | Run `pipeline.validate_scene()`, freeze transforms, delete history |
| **Custom commands / plugins** | Studio-specific commands the agent should know about? | List them with one-line descriptions |

If you don't know one of these, put a `TODO` placeholder in the prompt rather than guessing. Document it for the user.

## Anatomy of the template

The template at `assets/agent_system_prompt.md` is structured as:

1. **Role + scope** — what the agent is for.
2. **Operating environment** — Maya version, units, conventions (this is the placeholder-heavy section).
3. **How to use the tools** — selection model, query-before-mutate, undo chunks, API choice.
4. **Failure handling** — what to do when a tool errors; when to ask the user vs. retry.
5. **What "done" looks like** — when to stop and report vs. keep going.
6. **Pipeline-specific knowledge** — the customization block.

Each section is short. The whole template is ~80 lines. Pipelines that need more should add a "Pipeline-specific knowledge" appendix rather than expanding the core sections.

## Worked examples of decisions the prompt encodes

These are the discriminating cases — the prompt should produce the right behavior in each:

### Case 1: "Move the cube up 5 units"

Bad agent behavior:
```python
cmds.move(0, 5, 0, 'pCube1')   # assumes the cube is named pCube1
```

Good agent behavior:
```python
# First, confirm the object the user means
cubes = cmds.ls(type='mesh') and cmds.listRelatives(cmds.ls(type='mesh'), parent=True)
# ...check selection, ask user if ambiguous, then:
cmds.undoInfo(openChunk=True, chunkName='Move cube up 5')
try:
    cmds.move(0, 5, 0, target_cube, relative=True)
finally:
    cmds.undoInfo(closeChunk=True)
```

The prompt should encode: confirm-the-target, wrap-in-undo-chunk, use-relative-move-when-the-user-says-"up-5".

### Case 2: "Delete the unused materials"

Bad agent behavior: calls `cmds.delete(cmds.ls(materials=True))` and obliterates the default lambert.

Good agent behavior: uses `cmds.hyperShade(listNonUsed=True)` or queries shading groups, excludes Maya's built-in nodes, asks for confirmation if the count is > 0.

The prompt should encode: be paranoid about destructive operations on built-ins, prefer purpose-built commands over manual filters.

### Case 3: "Make the mesh smoother"

Bad agent behavior: starts wrapping `MFnMesh.subdivideFaces` in OpenMaya.

Good agent behavior: uses `cmds.polySmooth` first; only reaches for OpenMaya if `cmds` literally can't do what's asked.

The prompt should encode: cmds-first bias.

## Optional: tool-specific addenda

Some tools have nuances the schema can't express. The MCP SDK lets you put a `description` on each tool — use it for behavioral notes the LLM should know. Example:

```python
@mcp.tool(description=(
    "Save the current scene. If `path` is omitted, saves to the current "
    "scene file. Will REFUSE to save to a path under /pipeline/published/ "
    "— that requires the publish_asset tool instead."
))
def save_scene(path: str | None = None) -> str:
    ...
```

These addenda are read by the LLM on every call. Keep them tight.

## Testing the prompt

After generating the prompt, test it with the three "discriminating case" scenarios above plus 2–3 pipeline-specific scenarios the user describes. Look at the transcripts and check:

- Did the agent query before mutating?
- Did it wrap multi-step operations in an undo chunk?
- Did it use `cmds` where `cmds` was sufficient?
- Did it ask the user for confirmation on ambiguous targets?

If any of these fail consistently, the relevant section of the prompt needs strengthening — usually with a worked example rather than a louder MUST.
