"""Exports a full-scene H.264 MP4 capture, named per the studio's
review-file convention, and publishes it to Kitsu as the task's new
revision — using the currently open and logged-in Kitsu panel's
already-resolved project/task rather than requiring its own login step.

Deliberately independent of the existing single-frame Publish flow in
panel.py: naming conventions are one of the most studio-specific parts
of a pipeline, so keeping this as its own menu action (rather than
folding it into the existing Publish button) makes it easy to swap the
naming convention out later — see capture_naming.py — without touching
the core publish path.
"""

import os
import shutil

from . import maya_scene, video_export
from .capture_naming import build_capture_filename
from .versioning import parse_version_number


class PublishCaptureError(Exception):
    """A specific, user-facing reason the capture export failed."""


def ensure_ready(panel):
    """Raise PublishCaptureError with a clear reason if `panel` isn't in
    a state Publish Capture can work from (open, logged in, task
    detected); otherwise return normally. Called up front — before
    prompting for a status/comment — so the dialog is never shown only
    to fail immediately after.
    """
    if not (panel and panel.session and panel.project):
        raise PublishCaptureError(
            "Open the Kitsu panel, log in, and detect a task first."
        )
    if not (panel.task and panel.task_breadcrumb):
        raise PublishCaptureError("Detect a task before exporting a capture.")


def run(panel, task_status, comment=""):
    """Render, encode, and publish a named full-scene capture video to
    Kitsu as a new revision on the panel's detected task.

    `panel` is the active KitsuPanel instance; `task_status` is one of
    the status dicts from `panel.session.all_task_statuses(...)` (as
    shown in the Publish Capture dialog). Returns the output file path.
    """
    ensure_ready(panel)

    scene_path = maya_scene.scene_path()
    if not scene_path:
        raise PublishCaptureError(
            "This scene has not been saved yet, so it has no version "
            "number or output folder to use."
        )

    try:
        user = panel.session.current_user()
    except Exception as exc:
        raise PublishCaptureError(
            f"Could not resolve the logged-in user: {exc}"
        ) from exc

    version = parse_version_number(scene_path)
    show_name = panel.project.get("name", "")
    filename = build_capture_filename(
        show_name, panel.task_breadcrumb, panel.task_context, version, user
    )

    output_dir = os.path.join(os.path.dirname(scene_path), "capture")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{filename}.mp4")

    start_frame, end_frame = maya_scene.playback_range()
    fps = maya_scene.scene_fps()

    sequence_dir = os.path.join(output_dir, f".{filename}_frames_tmp")
    os.makedirs(sequence_dir, exist_ok=True)
    try:
        pattern, first_number, _frame_count = maya_scene.capture_viewport_sequence(
            sequence_dir, start_frame, end_frame
        )
        video_export.encode_h264_mp4(pattern, first_number, fps, output_path)
    finally:
        shutil.rmtree(sequence_dir, ignore_errors=True)

    try:
        panel.session.publish_preview(
            panel.task,
            task_status,
            comment=comment,
            preview_file_path=output_path,
            revision=version,
        )
    except Exception as exc:
        # The video itself rendered fine and is sitting on disk at
        # output_path — only the upload failed, so say so explicitly
        # rather than leaving the impression the whole export was lost.
        raise PublishCaptureError(
            f"The video was exported to {output_path} but could not be "
            f"uploaded to Kitsu: {exc}"
        ) from exc

    return output_path
