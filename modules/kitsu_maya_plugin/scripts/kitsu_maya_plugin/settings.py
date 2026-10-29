"""Persisted connection settings for the Kitsu/Zou plugin.

Everything here is safe to write to disk in plain text (server URL, email,
project id, optional API token). The account password is intentionally never
part of this module — it only ever lives in memory for the current Maya
session.
"""

import hashlib

from .qt import QSettings

_ORG = "Kitsu"
_APP = "MayaPlugin"

_KEYS = ("server_url", "email", "project_id", "api_token")


def load():
    settings = QSettings(_ORG, _APP)
    return {key: settings.value(key, "") or "" for key in _KEYS}


def save(values):
    settings = QSettings(_ORG, _APP)
    for key in _KEYS:
        if key in values:
            settings.setValue(key, values[key])
    settings.sync()


def load_section_collapsed(name):
    settings = QSettings(_ORG, _APP)
    # QSettings round-trips bools as the strings "true"/"false" on some
    # platforms; PySide has no `type=` keyword like PyQt5, so normalize
    # explicitly.
    value = settings.value(f"section_collapsed_{name}", False)
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def save_section_collapsed(name, collapsed):
    settings = QSettings(_ORG, _APP)
    settings.setValue(f"section_collapsed_{name}", collapsed)


def _override_key(kind, scene_path):
    digest = hashlib.sha1(scene_path.encode("utf-8")).hexdigest()
    return f"{kind}_override_{digest}"


def _load_override(kind, scene_path):
    settings = QSettings(_ORG, _APP)
    value = settings.value(_override_key(kind, scene_path), "")
    return value or None


def _save_override(kind, scene_path, entity_id):
    settings = QSettings(_ORG, _APP)
    settings.setValue(_override_key(kind, scene_path), entity_id)
    settings.sync()


def load_task_type_override(scene_path):
    """Return the manually-picked task-type id remembered for this exact
    file path, or None if there isn't one (e.g. never set, or the file
    was since renamed/moved — falls back to normal folder detection)."""
    return _load_override("task_type", scene_path)


def save_task_type_override(scene_path, task_type_id):
    _save_override("task_type", scene_path, task_type_id)


def load_shot_override(scene_path):
    """Return the manually-picked shot id remembered for this exact file
    path, or None — same lifetime rules as load_task_type_override."""
    return _load_override("shot", scene_path)


def save_shot_override(scene_path, shot_id):
    _save_override("shot", scene_path, shot_id)


def clear_overrides(scene_path):
    """Forget both manual picks for this file, so detection goes back to
    reading its folders."""
    settings = QSettings(_ORG, _APP)
    for kind in ("shot", "task_type"):
        settings.remove(_override_key(kind, scene_path))
    settings.sync()
