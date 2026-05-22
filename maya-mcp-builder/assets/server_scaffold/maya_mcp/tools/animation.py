"""Animation tools: playback range, keyframe queries, and range editing.

Every tool body imports Maya modules *inside* the inner ``_do`` function
and routes execution through ``bridge.run_main_thread``. Top-level
imports of ``maya.*`` are wrong here — this file is imported by the
FastMCP worker thread before Maya guarantees safe access.

See ``references/architecture.md`` in the maya-mcp-builder skill.
"""
from __future__ import annotations

from maya_mcp.bridge import run_main_thread
from maya_mcp.server import mcp

# Mapping of Maya time-unit strings to fps floats.
# Extended with additional Maya time strings beyond the required 6.
_FPS_MAP: dict[str, float] = {
    "film": 24.0,
    "pal": 25.0,
    "ntsc": 30.0,
    "show": 48.0,
    "palf": 50.0,
    "ntscf": 60.0,
    # Additional common Maya time strings
    "game": 15.0,
    "hour": 1.0 / 3600.0,
    "min": 1.0 / 60.0,
    "sec": 1.0,
    "millisec": 1000.0,
    "2fps": 2.0,
    "3fps": 3.0,
    "4fps": 4.0,
    "5fps": 5.0,
    "6fps": 6.0,
    "8fps": 8.0,
    "10fps": 10.0,
    "12fps": 12.0,
    "16fps": 16.0,
    "20fps": 20.0,
    "40fps": 40.0,
    "75fps": 75.0,
    "80fps": 80.0,
    "100fps": 100.0,
    "120fps": 120.0,
    "125fps": 125.0,
    "150fps": 150.0,
    "200fps": 200.0,
    "240fps": 240.0,
    "250fps": 250.0,
    "300fps": 300.0,
    "375fps": 375.0,
    "400fps": 400.0,
    "500fps": 500.0,
    "600fps": 600.0,
    "750fps": 750.0,
    "1200fps": 1200.0,
    "1500fps": 1500.0,
    "2000fps": 2000.0,
    "3000fps": 3000.0,
    "6000fps": 6000.0,
    "23.976fps": 24000.0 / 1001.0,
    "29.97fps": 30000.0 / 1001.0,
    "47.952fps": 48000.0 / 1001.0,
    "59.94fps": 60000.0 / 1001.0,
}


def _parse_fps(time_str: str) -> float | None:
    """Convert a Maya time-unit string to fps float, or None if unknown.

    Tries the ``_FPS_MAP`` lookup first. As a fallback, if the string ends
    in ``"fps"`` and the prefix is a valid number, that number is returned.
    Returns ``None`` for unrecognised strings (e.g. exotic custom rates).
    """
    result = _FPS_MAP.get(time_str)
    if result is not None:
        return result
    # Fallback: parse strings like "23.976fps" or "48fps" not in the map.
    if time_str.endswith("fps"):
        try:
            return float(time_str[:-3])
        except ValueError:
            pass
    return None


@mcp.tool()
def query_animation_range() -> dict:
    """Return the current playback range and frame rate.

    Returns:
        Dict with keys:
            - ``min_frame`` (float): Start of playback range.
            - ``max_frame`` (float): End of playback range.
            - ``fps_string`` (str): Maya time-unit string (e.g. ``"film"``).
            - ``fps`` (float | None): Frames per second. ``None`` if the
              time-unit string is unrecognised.

    Example:
        >>> query_animation_range()
        {'min_frame': 1.0, 'max_frame': 120.0, 'fps_string': 'film', 'fps': 24.0}

    Wraps maya.cmds.playbackOptions and maya.cmds.currentUnit.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/playbackOptions.html
    """
    def _do() -> dict:
        import maya.cmds as cmds  # INSIDE _do(), never at module top

        min_frame: float = cmds.playbackOptions(query=True, minTime=True)
        max_frame: float = cmds.playbackOptions(query=True, maxTime=True)
        fps_string: str = cmds.currentUnit(query=True, time=True)
        fps = _parse_fps(fps_string)
        return {
            "min_frame": min_frame,
            "max_frame": max_frame,
            "fps_string": fps_string,
            "fps": fps,
        }

    return run_main_thread(_do)


@mcp.tool()
def get_keyframes(node: str, attribute: str | None = None) -> list[dict]:
    """List all keyframes on a node, optionally filtered to one attribute.

    Args:
        node: Node name (e.g. ``pCube1``).
        attribute: Attribute long name (e.g. ``translateX``). If omitted,
            all keyed attributes on the node are returned.

    Returns:
        List of dicts, each with keys ``frame`` (float), ``value`` (float),
        and ``attribute`` (str). Sorted by frame, then attribute name.
        Returns an empty list if no keyframes exist.

    Raises:
        ValueError: If the node does not exist, or if ``attribute`` is
            given but does not exist on the node.

    Example:
        >>> get_keyframes('pCube1', 'translateX')
        [{'frame': 1.0, 'value': 0.0, 'attribute': 'translateX'},
         {'frame': 24.0, 'value': 5.0, 'attribute': 'translateX'}]

    Wraps maya.cmds.keyframe and maya.cmds.listAttr.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/keyframe.html
    """
    def _do() -> list[dict]:
        import maya.cmds as cmds  # INSIDE _do(), never at module top

        if not cmds.objExists(node):
            raise ValueError(f"No node named {node!r} in the scene")

        results: list[dict] = []

        if attribute is not None:
            # Validate that the attribute exists on the node.
            plug = f"{node}.{attribute}"
            if not cmds.objExists(plug):
                raise ValueError(f"Node {node!r} has no attribute {attribute!r}")

            times = cmds.keyframe(
                node, attribute=attribute, query=True, timeChange=True
            )
            values = cmds.keyframe(
                node, attribute=attribute, query=True, valueChange=True
            )
            # cmds.keyframe returns None when no keyframes exist.
            if times is not None and values is not None:
                for t, v in zip(times, values):
                    results.append({"frame": float(t), "value": float(v), "attribute": attribute})
        else:
            # Iterate over all keyable attributes; filter to those with keys.
            keyable_attrs = cmds.listAttr(node, keyable=True)
            if keyable_attrs:
                for attr in keyable_attrs:
                    try:
                        times = cmds.keyframe(
                            node, attribute=attr, query=True, timeChange=True
                        )
                        values = cmds.keyframe(
                            node, attribute=attr, query=True, valueChange=True
                        )
                    except Exception:  # noqa: BLE001 — compound attrs raise MayaCommandError
                        continue
                    # Skip attributes that have no keyframes (returns None).
                    if times is None or values is None:
                        continue
                    for t, v in zip(times, values):
                        results.append({"frame": float(t), "value": float(v), "attribute": attr})

        results.sort(key=lambda k: (k["frame"], k["attribute"]))
        return results

    return run_main_thread(_do)


@mcp.tool()
def set_animation_range(min_frame: int, max_frame: int) -> dict:
    """Set the scene playback and animation range.

    Args:
        min_frame: New start frame. Must be strictly less than ``max_frame``.
        max_frame: New end frame.

    Returns:
        Dict with keys:
            - ``min_frame`` (int): The new start frame that was set.
            - ``max_frame`` (int): The new end frame that was set.
            - ``previous_min`` (float): The start frame before this call.
            - ``previous_max`` (float): The end frame before this call.

    Raises:
        ValueError: If ``min_frame`` is not strictly less than ``max_frame``.

    Example:
        >>> set_animation_range(1, 120)
        {'min_frame': 1, 'max_frame': 120, 'previous_min': 1.0, 'previous_max': 24.0}

    Wraps maya.cmds.playbackOptions.
    Docs: https://help.autodesk.com/cloudhelp/2026/ENU/Maya-Tech-Docs/CommandsPython/playbackOptions.html
    """
    # Validate BEFORE touching the undo stack so bad input is clean.
    if min_frame >= max_frame:
        raise ValueError(
            f"min_frame ({min_frame}) must be strictly less than max_frame ({max_frame})"
        )

    def _do() -> dict:
        import maya.cmds as cmds  # INSIDE _do(), never at module top

        # Capture the previous range before mutation.
        previous_min: float = cmds.playbackOptions(query=True, minTime=True)
        previous_max: float = cmds.playbackOptions(query=True, maxTime=True)

        cmds.undoInfo(openChunk=True, chunkName="mcp:set_animation_range")
        try:
            cmds.playbackOptions(
                minTime=min_frame,
                maxTime=max_frame,
                animationStartTime=min_frame,
                animationEndTime=max_frame,
            )
        finally:
            cmds.undoInfo(closeChunk=True)

        return {
            "min_frame": min_frame,
            "max_frame": max_frame,
            "previous_min": previous_min,
            "previous_max": previous_max,
        }

    return run_main_thread(_do)
