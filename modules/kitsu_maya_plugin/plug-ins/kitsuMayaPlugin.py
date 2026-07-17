"""Maya plug-in entry point for the Kitsu/Zou integration.

Loading this plug-in (Windows > Settings/Preferences > Plug-in Manager >
kitsuMayaPlugin.py) adds a top-level "Kitsu/Zou" menu to Maya's main
window; unloading removes it again. The actual panel code lives in the
`kitsu_maya_plugin` Python package (this module's scripts/ sibling, put
on the path by the .mod file) and is only imported when the menu entry is
actually clicked — so a problem in the panel or the vendored network
libraries can never prevent the plug-in itself from loading.

Named kitsuMayaPlugin.py (not kitsu_maya_plugin.py) on purpose: Maya
registers a Python plug-in in sys.modules under its file name, which
would otherwise collide with the package of the same name.
"""

import maya.api.OpenMaya as om
from maya import cmds, mel

_MENU = "kitsuZouMenu"
_VERSION = "1.11.3"


def maya_useNewAPI():
    """Tell Maya this plug-in uses the Python API 2.0."""


def _build_menu(*_args):
    if cmds.about(batch=True):
        return
    if cmds.menu(_MENU, exists=True):
        cmds.deleteUI(_MENU, menu=True)
    main_window = mel.eval("$kitsuTmpMainWindow = $gMainWindow;")
    cmds.menu(_MENU, label="Kitsu/Zou", parent=main_window, tearOff=False)
    cmds.menuItem(label="Open Kitsu Panel", parent=_MENU, command=_open_panel)
    cmds.menuItem(label="Add Shelf Button", parent=_MENU, command=_add_shelf_button)
    cmds.menuItem(label="Publish Capture", parent=_MENU, command=_publish_capture)


def _open_panel(*_args):
    from kitsu_maya_plugin import panel

    panel.show_panel()


def _add_shelf_button(*_args):
    from kitsu_maya_plugin import shelf

    shelf.add_shelf_button()


def _publish_capture(*_args):
    from kitsu_maya_plugin import panel, publish_capture

    active_panel = panel.get_active_panel()
    try:
        publish_capture.ensure_ready(active_panel)
    except publish_capture.PublishCaptureError as exc:
        cmds.confirmDialog(title="Kitsu", message=str(exc), button=["OK"])
        return

    try:
        statuses = active_panel.session.all_task_statuses(active_panel.project)
    except Exception as exc:
        cmds.confirmDialog(
            title="Kitsu",
            message=f"Could not load task statuses: {exc}",
            button=["OK"],
        )
        return

    from kitsu_maya_plugin.capture_dialog import prompt_for_status_and_comment

    prompted = prompt_for_status_and_comment(statuses)
    if prompted is None:
        return  # cancelled, or no status available to select
    task_status, comment = prompted

    try:
        output_path = publish_capture.run(active_panel, task_status, comment)
    except publish_capture.PublishCaptureError as exc:
        cmds.confirmDialog(title="Kitsu", message=str(exc), button=["OK"])
        return
    except Exception as exc:
        cmds.confirmDialog(
            title="Kitsu", message=f"Capture export failed: {exc}", button=["OK"]
        )
        return
    cmds.confirmDialog(
        title="Kitsu",
        message=f"Capture published to Kitsu and saved to:\n{output_path}",
        button=["OK"],
    )


def initializePlugin(plugin):
    om.MFnPlugin(plugin, "kitsu-maya-plugin", _VERSION, "Any")
    # Deferred: on auto-load during startup the main window (and its menu
    # bar) doesn't exist yet when plug-ins initialize.
    cmds.evalDeferred(_build_menu)


def uninitializePlugin(plugin):
    om.MFnPlugin(plugin)
    if not cmds.about(batch=True) and cmds.menu(_MENU, exists=True):
        cmds.deleteUI(_MENU, menu=True)
