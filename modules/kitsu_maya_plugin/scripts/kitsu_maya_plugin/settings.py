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


def _task_type_override_key(scene_path):
    digest = hashlib.sha1(scene_path.encode("utf-8")).hexdigest()
    return f"task_type_override_{digest}"


def load_task_type_override(scene_path):
    """Return the manually-picked task-type id remembered for this exact
    file path, or None if there isn't one (e.g. never set, or the file
    was since renamed/moved — falls back to normal folder detection)."""
    settings = QSettings(_ORG, _APP)
    value = settings.value(_task_type_override_key(scene_path), "") or ""
    return value or None


def save_task_type_override(scene_path, task_type_id):
    settings = QSettings(_ORG, _APP)
    settings.setValue(_task_type_override_key(scene_path), task_type_id)
    settings.sync()
