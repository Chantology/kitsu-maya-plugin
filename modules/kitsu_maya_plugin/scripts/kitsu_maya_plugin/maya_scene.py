"""Maya-side integration: everything that talks to Maya itself.

This is the Maya counterpart of what the Krita version of this plugin did
through `Krita.instance()` — kept in one module so the rest of the code
(panel UI, Kitsu API wrapper, task detection, drawover rendering) stays
host-agnostic:

- the current scene's file path (for task detection + version parsing)
- a single-frame viewport capture (what gets published as the preview,
  the equivalent of Krita's "export the canvas to PNG")
- a "Feedback_<revision>" image plane showing a review drawover in the
  viewport (the equivalent of Krita's "add drawover as a paint layer")
"""

import glob
import os
import re
import shutil
import tempfile

from maya import cmds, mel


class MayaSceneError(Exception):
    """A specific, user-facing reason a Maya-side operation failed."""


def scene_path():
    """Absolute path of the current scene, or None if it was never saved."""
    path = cmds.file(query=True, sceneName=True)
    return path or None


def scene_fps():
    """Frames per second of the scene's current time unit."""
    return mel.eval("currentTimeUnitToFPS")


def playback_start():
    """First frame of the playback range — frame 0 of a published playblast."""
    return int(cmds.playbackOptions(query=True, minTime=True))


def playback_range():
    """(start, end) frames of the current playback range, inclusive —
    what a full-scene capture (see capture_viewport_sequence) covers."""
    return (
        int(cmds.playbackOptions(query=True, minTime=True)),
        int(cmds.playbackOptions(query=True, maxTime=True)),
    )


def render_resolution():
    """The scene's render resolution (width, height)."""
    return (
        cmds.getAttr("defaultResolution.width"),
        cmds.getAttr("defaultResolution.height"),
    )


def capture_viewport(target_path):
    """Playblast the current frame of the active viewport to `target_path`.

    Uses a single-frame image playblast rather than a renderer so what
    gets published is exactly what the artist sees in the viewport,
    including HUD-free shading — the closest Maya equivalent of Krita's
    canvas export.
    """
    frame = int(cmds.currentTime(query=True))

    # Playblast inserts its own frame padding into the output name even
    # for a single frame, so capture into a private temp dir and move the
    # produced file to the exact requested path instead of predicting it.
    capture_dir = tempfile.mkdtemp(prefix="kitsu_capture_")
    prefix = os.path.join(capture_dir, "capture")
    try:
        cmds.playblast(
            filename=prefix,
            format="image",
            compression="png",
            frame=[frame],
            viewer=False,
            showOrnaments=False,
            percent=100,
            quality=100,
            framePadding=4,
            forceOverwrite=True,
        )
        produced = sorted(glob.glob(prefix + "*"))
        if not produced:
            raise MayaSceneError(
                "Playblast produced no image — make sure a viewport (model "
                "panel) is visible."
            )
        shutil.move(produced[0], target_path)
    finally:
        shutil.rmtree(capture_dir, ignore_errors=True)

    return target_path


def capture_viewport_sequence(directory, start_frame, end_frame):
    """Playblast the active viewport over [start_frame, end_frame] as a PNG
    sequence into `directory`, for encoding into a full-scene video (see
    video_export.encode_h264_mp4).

    Returns (pattern, first_frame_number, frame_count): `pattern` is a
    printf-style ('%0Nd') path ffmpeg can consume directly. The exact
    zero-padding/numbering Maya's playblast writes for a frame range
    isn't asserted upfront — instead the produced files are inspected
    after the fact and the pattern built to match, so this doesn't depend
    on a specific Maya version's playblast naming behavior holding true.
    """
    prefix = os.path.join(directory, "capture")
    cmds.playblast(
        filename=prefix,
        format="image",
        compression="png",
        startTime=start_frame,
        endTime=end_frame,
        viewer=False,
        showOrnaments=False,
        percent=100,
        quality=100,
        framePadding=4,
        forceOverwrite=True,
    )
    produced = sorted(glob.glob(prefix + ".*.*"))
    if not produced:
        raise MayaSceneError(
            "Playblast produced no frames — make sure a viewport (model "
            "panel) is visible and the playback range is valid."
        )

    match = re.search(r"\.(\d+)\.(\w+)$", os.path.basename(produced[0]))
    if not match:
        raise MayaSceneError(
            f"Unexpected playblast output file name: {os.path.basename(produced[0])!r}"
        )
    first_number_str, ext = match.groups()
    padding = len(first_number_str)
    pattern = f"{prefix}.%0{padding}d.{ext}"
    return pattern, int(first_number_str), len(produced)


def _active_camera():
    """Camera of the focused model panel, or of any visible one."""
    panel = cmds.getPanel(withFocus=True)
    if not (panel and cmds.getPanel(typeOf=panel) == "modelPanel"):
        panel = None
        for candidate in cmds.getPanel(visiblePanels=True) or []:
            if cmds.getPanel(typeOf=candidate) == "modelPanel":
                panel = candidate
                break
    if panel is None:
        return None
    return cmds.modelPanel(panel, query=True, camera=True)


def set_feedback_image_plane(image_path, name):
    """Show `image_path` as an image plane named `name` in the viewport.

    Attached to the active viewport's camera when one can be determined
    (so it frames the view like Kitsu's own review overlay would),
    otherwise created as a free image plane. An existing plane with the
    same name is replaced rather than stacked, so re-clicking the same
    revision never accumulates duplicates — mirroring the Krita version's
    replace-the-layer behavior.
    """
    for existing in cmds.ls(name, type="transform", long=True):
        cmds.delete(existing)

    camera = _active_camera()
    if camera:
        transform, _shape = cmds.imagePlane(
            camera=camera, fileName=image_path, showInAllViews=False
        )
    else:
        transform, _shape = cmds.imagePlane(fileName=image_path)

    return cmds.rename(transform, name)


_BOOKMARK_PLUGIN = "timeSliderBookmark"
_BOOKMARK_COLOR = (0.26, 0.71, 0.55)  # teal-green, distinct from Maya's defaults


def set_timeline_bookmarks(prefix, frame_labels):
    """Create one single-frame time-slider bookmark per annotated frame.

    `frame_labels` maps a Maya frame number to the bookmark's label. Any
    existing bookmark whose label starts with `prefix` is removed first,
    so re-clicking for the same revision replaces its markers instead of
    stacking duplicates (same contract as the feedback image planes).

    Time Slider Bookmarks are Maya's native timeline markers (Maya 2020+,
    shipped as the timeSliderBookmark plug-in): they show as colored bands
    on the time slider with their label on hover, are listed under
    Windows > Animation Editors > Time Slider Bookmarks, and are plain DG
    nodes — deleting the Kitsu_* nodes removes the markers again.
    """
    try:
        cmds.loadPlugin(_BOOKMARK_PLUGIN, quiet=True)
    except Exception as exc:
        raise MayaSceneError(
            f"Time Slider Bookmarks are not available (needs Maya 2020+): {exc}"
        ) from exc
    if not cmds.pluginInfo(_BOOKMARK_PLUGIN, query=True, loaded=True):
        raise MayaSceneError(
            "Time Slider Bookmarks are not available (the timeSliderBookmark "
            "plug-in could not be loaded — needs Maya 2020+)."
        )

    for node in cmds.ls(type="timeSliderBookmark") or []:
        if (cmds.getAttr(node + ".name") or "").startswith(prefix):
            cmds.delete(node)

    # Mark the plug-in as a requirement of the scene only if it was
    # actually used, matching Maya's own bookmark helper.
    cmds.pluginInfo(_BOOKMARK_PLUGIN, edit=True, writeRequires=True)

    created = []
    for frame, label in sorted(frame_labels.items()):
        node = cmds.createNode("timeSliderBookmark")
        cmds.setAttr(node + ".name", label, type="string")
        cmds.setAttr(node + ".timeRangeStart", frame)
        cmds.setAttr(node + ".timeRangeStop", frame + 1)
        cmds.setAttr(node + ".color", *_BOOKMARK_COLOR)
        created.append(node)
    return created


def set_feedback_drawover_sequence(first_file, frames, blank_frame, name):
    """Show a per-frame drawover sequence as an image plane named `name`.

    `first_file` is one file of an image sequence whose files are numbered
    by Maya frame (`<base>.<frame padded to 4>.png`): each frame in
    `frames` has that frame's drawover on a transparent background, and
    `blank_frame` is a fully transparent image. An expression drives the
    plane's frameExtension so annotated frames show their own drawover and
    every other frame shows the blank — meaning markup only ever appears
    on the exact frames the reviewer drew on.

    Like set_feedback_image_plane, an existing plane with the same name is
    replaced rather than stacked.
    """
    for existing in cmds.ls(name, type="transform", long=True):
        cmds.delete(existing)

    camera = _active_camera()
    if camera:
        transform, shape = cmds.imagePlane(
            camera=camera, fileName=first_file, showInAllViews=False
        )
    else:
        transform, shape = cmds.imagePlane(fileName=first_file)

    cmds.setAttr(shape + ".useFrameExtension", 1)
    # Enabling useFrameExtension auto-wires scene time into frameExtension
    # (through a timeToUnitConversion node); break that connection at the
    # plug so our frame-mapping expression can drive the attribute instead.
    plug = shape + ".frameExtension"
    source_plug = cmds.connectionInfo(plug, sourceFromDestination=True)
    if source_plug:
        cmds.disconnectAttr(source_plug, plug)
        source_node = source_plug.split(".")[0]
        if cmds.nodeType(source_node) in ("timeToUnitConversion", "unitConversion"):
            cmds.delete(source_node)

    lines = ["int $f = (int)frame;", f"int $e = {int(blank_frame)};"]
    for frame in sorted(frames):
        lines.append(f"if ($f == {int(frame)}) $e = {int(frame)};")
    lines.append("frameExtension = $e;")
    cmds.expression(string="\n".join(lines), object=shape)

    return cmds.rename(transform, name)
