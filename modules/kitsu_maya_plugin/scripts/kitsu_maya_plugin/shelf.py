"""Adds an optional Maya shelf button that opens the Kitsu panel.

Not required for the plug-in to work — the "Kitsu/Zou" menu (added by
kitsuMayaPlugin.py) already opens the panel. This is a convenience for
anyone who wants a persistent, one-click shelf icon instead of going
through the menu each time, wired up via the "Kitsu/Zou > Add Shelf
Button" menu entry.

The icon (icons/kitsu_maya_plugin.png, plus an @2x variant for HiDPI) is
resolved through XBMLANGPATH rather than an absolute path: Maya modules
automatically add their own icons/ folder to XBMLANGPATH (the same
mechanism scripts/ relies on for MAYA_SCRIPT_PATH/PYTHONPATH), so passing
just the file name works regardless of where the module ends up
installed — exactly how Maya resolves its own built-in shelf icons.
"""

from maya import cmds, mel

_ICON_NAME = "kitsu_maya_plugin.png"

# Kept in one place so a shelf button created by an older version of this
# module (a different command string) is recognized and replaced instead
# of leaving a second, stale button behind.
_COMMAND = (
    "import maya.cmds as cmds\n"
    "if not cmds.pluginInfo('kitsuMayaPlugin', query=True, loaded=True):\n"
    "    cmds.loadPlugin('kitsuMayaPlugin')\n"
    "from kitsu_maya_plugin import panel\n"
    "panel.show_panel()\n"
)


def _current_shelf():
    top_shelf = mel.eval("$kitsuTmpShelfTopLevel = $gShelfTopLevel;")
    return cmds.tabLayout(top_shelf, query=True, selectTab=True)


def add_shelf_button(shelf_name=None):
    """Add a "Kitsu" button to `shelf_name` (the active shelf if None).

    Safe to call more than once: an existing Kitsu button on that shelf
    (recognized by command, not label, in case a studio renames it) is
    removed first rather than duplicated.
    """
    if shelf_name is None:
        shelf_name = _current_shelf()

    for existing in cmds.shelfLayout(shelf_name, query=True, childArray=True) or []:
        if cmds.shelfButton(existing, query=True, command=True) == _COMMAND:
            cmds.deleteUI(existing)

    return cmds.shelfButton(
        parent=shelf_name,
        label="Kitsu",
        annotation="Open the Kitsu panel",
        image1=_ICON_NAME,
        command=_COMMAND,
        sourceType="python",
        style="iconOnly",
    )
