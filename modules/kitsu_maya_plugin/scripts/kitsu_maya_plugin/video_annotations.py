"""Extract per-frame drawover annotations from a Kitsu video preview.

For still images Kitsu stores a single annotation entry at time == 0
(handled by annotations.py); for movie previews it stores one entry per
annotated frame, keyed by playback time in seconds:

    {"time": 3.2, "drawing": {"objects": [...]}}

Kitsu's own player converts between time and frame with the production's
fps (frame = round(time * fps)), so the same conversion is applied here.
Newer Zou versions also record the frame number directly on the entry;
when present it is trusted over the time-based computation, EXCEPT that
it is treated here as 1-based (the first frame is reported as 1, not 0)
and normalized down by one to match the time-based path's own 0-based
result. Confirmed against a real production Kitsu/Zou server: markers/
drawovers landed exactly one frame later than Kitsu's own display,
consistently, until this normalization was added (see the Blender port,
where this was first caught and fixed, for the fuller investigation
notes).

Frames are returned 0-based, exactly as Kitsu counts them internally —
mapping them onto the Maya timeline (offsetting by the playback start
frame, since frame 0 of a published playblast is the first frame of the
playback range) is the caller's job.
"""


def annotated_frames(preview, fps):
    """Return {frame (int, 0-based): [fabric shape dicts]} for a video.

    Entries without any drawn objects are skipped, so every returned
    frame actually has visible markup.
    """
    result = {}
    if not fps or fps <= 0:
        return result

    for entry in preview.get("annotations") or []:
        objects = (entry.get("drawing") or {}).get("objects") or []
        if not objects:
            continue

        frame = entry.get("frame")
        if frame is not None:
            frame = frame - 1  # Zou's "frame" field is 1-based; normalize to 0-based
        else:
            frame = round(float(entry.get("time") or 0) * fps)
        result.setdefault(int(frame), []).extend(objects)

    return result
