"""Encodes a Maya playblast image sequence into an H.264 MP4.

Maya's own `playblast` command can't produce H.264/MP4 directly on most
current installs — that used to go through QuickTime, which Apple
discontinued for Windows in 2016 and hasn't shipped by default on macOS
either for years — so this renders a PNG sequence via
maya_scene.capture_viewport_sequence, then shells out to `ffmpeg`
(expected on the system PATH) to encode it. Most animation/VFX pipelines
already have ffmpeg installed for exactly this kind of job; if it's
missing, the error says so explicitly rather than failing in some more
obscure way inside Maya's own playblast codecs.
"""

import shutil
import subprocess
import sys

# Prevents a console window from flashing up behind Maya every time
# ffmpeg is launched on Windows; subprocess.CREATE_NO_WINDOW doesn't
# exist on other platforms.
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class VideoExportError(Exception):
    """A specific, user-facing reason a video export failed."""


def find_ffmpeg():
    return shutil.which("ffmpeg")


def encode_h264_mp4(sequence_pattern, start_number, fps, output_path):
    """Encode a printf-style ('%04d') image sequence, as produced by
    maya_scene.capture_viewport_sequence, into an H.264 MP4 at `fps`.

    `start_number` must match the first frame's actual number in
    `sequence_pattern` (ffmpeg needs this to locate the first file).
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise VideoExportError(
            "ffmpeg was not found on your system PATH. Install ffmpeg "
            "(https://ffmpeg.org/) and make sure it's on PATH to export "
            "video captures."
        )

    command = [
        ffmpeg,
        "-y",
        "-start_number", str(start_number),
        "-framerate", str(fps),
        "-i", sequence_pattern,
        "-c:v", "libx264",
        # libx264 requires even width/height for 4:2:0 chroma subsampling
        # (yuv420p) — a Maya viewport panel's actual pixel size is
        # essentially never guaranteed to be even, so without this filter
        # libx264 fails to open the encoder for an odd-sized capture with
        # an opaque "Error while opening encoder" rather than a clear
        # "odd dimensions" message. This rounds each dimension down to the
        # nearest even number (a 1px crop is imperceptible here).
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, creationflags=_SUBPROCESS_FLAGS
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-10:])
        raise VideoExportError(f"ffmpeg failed to encode the video:\n{tail}")
    return output_path
