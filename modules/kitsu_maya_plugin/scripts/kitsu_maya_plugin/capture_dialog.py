"""A small modal dialog collecting the Kitsu status + comment that
Publish Capture attaches to the exported video's revision — the same two
fields the panel's own single-frame Publish section already asks for,
just as a standalone dialog rather than reusing the panel's widgets
directly, since Publish Capture is meant to work as an independent menu
action regardless of which panel section happens to be visible/collapsed
at the time.
"""

from .qt import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    exec_dialog,
)


class CaptureCommentDialog(QDialog):
    def __init__(self, statuses, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Publish Capture")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Status"))
        self.status_combo = QComboBox()
        for status in statuses:
            self.status_combo.addItem(
                status.get("name", status.get("short_name", "?")), status
            )
        layout.addWidget(self.status_combo)

        layout.addWidget(QLabel("Comment"))
        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("Comment")
        layout.addWidget(self.comment_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_status(self):
        return self.status_combo.currentData()

    def comment_text(self):
        return self.comment_edit.toPlainText().strip()


def prompt_for_status_and_comment(statuses, parent=None):
    """Show the dialog modally. Returns (status_data, comment_text), or
    None if the user cancelled or no status could be selected."""
    dialog = CaptureCommentDialog(statuses, parent=parent)
    if exec_dialog(dialog) != QDialog.Accepted:
        return None
    status_data = dialog.selected_status()
    if status_data is None:
        return None
    return status_data, dialog.comment_text()
