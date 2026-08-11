"""Dockable panel UI for the Kitsu/Zou plugin.

The direct port of the Krita version's dock widget: same sections
(Connection / Task / Feedback / Publish), same behavior, with the
host-specific pieces swapped for their Maya equivalents via maya_scene —
publishing captures the active viewport instead of exporting the canvas,
and drawovers become camera image planes instead of paint layers.

Docking uses MayaQWidgetDockableMixin, which wraps the widget in a Maya
workspaceControl so it docks/tabs like the Attribute Editor or Outliner
and survives workspace switching.
"""

import os
import tempfile
import time
from datetime import date

from maya import cmds
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

from .qt import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPixmap,
    QPushButton,
    QScrollArea,
    Qt,
    QSlider,
    QTextEdit,
    QTimer,
    QVBoxLayout,
    QWidget,
)

from . import maya_scene, settings, video_annotations
from .annotations import picture_annotation_objects, render_annotations
from .collapsible_section import CollapsibleSection
from .kitsu_api import KitsuConnectionError, KitsuSession
from .task_detection import TaskDetectionError, TaskTypeMismatch, _path_is_shot_context, detect_task
from .versioning import parse_version_number

_OBJECT_NAME = "kitsuMayaPanel"
_WORKSPACE_CONTROL = _OBJECT_NAME + "WorkspaceControl"


class KitsuPanel(MayaQWidgetDockableMixin, QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(_OBJECT_NAME)
        self.setWindowTitle("Kitsu")

        self.session = None
        self.project = None
        self.current_user = None  # the logged-in Kitsu account (see _load_user_header)
        self.task = None
        self.task_breadcrumb = None  # resolved entity names, top-down (see task_detection.detect_task)
        self.task_context = None  # "asset" or "shot"
        self._pending_override_entity = None  # entity awaiting a manual task-type pick
        self._pending_override_breadcrumb = None
        self._pending_override_context = None  # "asset" or "shot", for the entity above
        self._pending_override_previous_task_id = None
        self._scene_path_for_override = None
        self._attachment_cache = {}  # attachment id -> local file path
        self._preview_cache = {}  # preview (revision) id -> local file path
        self._pending_variations = []  # local image paths queued to publish (first = main preview, rest = variations)
        self._last_comment_ids = None  # ids currently rendered in comments_list, for change detection

        self._comment_poll_timer = QTimer(self)
        self._comment_poll_timer.setInterval(60_000)  # 60s: background auto-refresh, no need for finer
        self._comment_poll_timer.timeout.connect(self._on_comment_poll_tick)
        self._comment_poll_timer.start()

        # Live time-tracking for the timesheet slider: while active, the
        # slider auto-advances from `_time_tracking_base_minutes` (today's
        # already-logged time as of the last server sync) by however many
        # minutes have elapsed since `_time_tracking_start`. Any manual
        # slider interaction turns it off until the next task
        # (re-)detection or a successful time log restarts it.
        self._time_tracking_active = False
        self._time_tracking_base_minutes = 0
        self._time_tracking_start = None
        self._time_tracking_timer = QTimer(self)
        self._time_tracking_timer.setInterval(30_000)  # 30s: plenty for minute-level display
        self._time_tracking_timer.timeout.connect(self._on_time_tracking_tick)

        layout = QVBoxLayout(self)

        self.status_label = QLabel("Not connected")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addWidget(self._build_section("connection", "Connection", self._build_connection_group()))
        layout.addWidget(self._build_section("task", "Task", self._build_task_group()))
        layout.addWidget(self._build_section("feedback", "Feedback", self._build_feedback_group()))
        layout.addWidget(self._build_section("publish", "Publish", self._build_publish_group()))
        layout.addStretch(1)

        self._load_settings()
        self._maybe_auto_login()

    def _maybe_auto_login(self):
        """Log in automatically if a server URL, project ID, and a saved
        token from a previous login (see _on_login_clicked) are all
        already filled in — that's the whole point of saving the token
        in the first place, rather than making every session start with
        a manual click. Deferred via QTimer.singleShot(0, ...) so the
        panel actually finishes appearing before the login's network
        call blocks the UI thread, instead of the panel only becoming
        visible once that call finishes.
        """
        if (
            self.server_edit.text().strip()
            and self.project_id_edit.text().strip()
            and self.token_edit.text().strip()
        ):
            QTimer.singleShot(0, self._on_login_clicked)

    # ---- UI construction ----

    def _build_section(self, name, title, content_widget):
        section = CollapsibleSection(
            title, content_widget, collapsed=settings.load_section_collapsed(name)
        )
        section.toggled.connect(
            lambda expanded: settings.save_section_collapsed(name, not expanded)
        )
        return section

    def _build_connection_group(self):
        group = QWidget()
        form = QFormLayout(group)

        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("https://kitsu.yourstudio.com")
        self.email_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setToolTip(
            "Paste the project's ID (a long hash) from its Kitsu URL. "
            "The project's name and thumbnail will show in the Task "
            "section once you log in."
        )
        self.project_id_edit.textChanged.connect(self._on_project_id_changed)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("Optional: skip email/password next time")
        self.token_edit.setToolTip(
            "Leave blank and log in with email/password once — this fills "
            "in and saves itself automatically afterward, so future logins "
            "can skip the password. It's a refresh token, not your "
            "password, and it eventually expires on its own (how long "
            "depends on the server); logging in with your password again "
            "any time just replaces it with a fresh one."
        )

        form.addRow("Server URL", self.server_edit)
        form.addRow("Email", self.email_edit)
        form.addRow("Password", self.password_edit)
        form.addRow("Project ID", self.project_id_edit)
        form.addRow("API token", self.token_edit)

        self.user_welcome_label = QLabel("")
        self.user_welcome_label.setAlignment(Qt.AlignHCenter)
        self.user_welcome_label.setStyleSheet("font-weight: bold;")
        self.user_welcome_label.setWordWrap(True)
        self.user_welcome_label.setVisible(False)
        form.addRow(self.user_welcome_label)

        self.user_thumbnail_label = QLabel()
        self.user_thumbnail_label.setAlignment(Qt.AlignHCenter)
        self.user_thumbnail_label.setVisible(False)
        form.addRow(self.user_thumbnail_label)

        self.login_button = QPushButton("Login")
        self.login_button.clicked.connect(self._on_login_clicked)
        form.addRow(self.login_button)

        return group

    _PROJECT_THUMBNAIL_WIDTH = 120

    def _build_task_group(self):
        group = QWidget()
        layout = QVBoxLayout(group)

        self.project_thumbnail_label = QLabel()
        self.project_thumbnail_label.setVisible(False)
        layout.addWidget(self.project_thumbnail_label)

        self.project_name_label = QLabel("")
        self.project_name_label.setStyleSheet("font-weight: bold;")
        self.project_name_label.setWordWrap(True)
        layout.addWidget(self.project_name_label)

        self.task_label = QLabel("No task detected")
        self.task_label.setWordWrap(True)
        layout.addWidget(self.task_label)

        self.task_status_label = QLabel("")
        self.task_status_label.setVisible(False)
        layout.addWidget(self.task_status_label)

        self.task_due_date_label = QLabel("")
        self.task_due_date_label.setVisible(False)
        layout.addWidget(self.task_due_date_label)

        self.task_time_spent_label = QLabel("")
        self.task_time_spent_label.setWordWrap(True)
        self.task_time_spent_label.setVisible(False)
        layout.addWidget(self.task_time_spent_label)

        self.time_entry_group = self._build_time_entry_group()
        self.time_entry_group.setVisible(False)
        layout.addWidget(self.time_entry_group)

        self.refresh_button = QPushButton("Detect Task")
        self.refresh_button.setToolTip("Detect task from current scene")
        self.refresh_button.clicked.connect(self._on_refresh_task_clicked)
        layout.addWidget(self.refresh_button)

        self.task_type_override_label = QLabel("")
        self.task_type_override_label.setWordWrap(True)
        self.task_type_override_label.setVisible(False)
        layout.addWidget(self.task_type_override_label)

        self.task_type_override_combo = QComboBox()
        self.task_type_override_combo.setVisible(False)
        self.task_type_override_combo.currentIndexChanged.connect(
            self._on_task_type_override_changed
        )
        layout.addWidget(self.task_type_override_combo)

        return group

    # Minutes, so the slider's actual step size can be a whole number —
    # Kitsu's own web timesheet slider works in half-hour increments too.
    _TIME_SLIDER_MAX_MINUTES = 12 * 60
    _TIME_SLIDER_STEP_MINUTES = 30
    _TIME_PRESETS_MINUTES = (60, 240, 480)  # 1h, 4h, 8h

    def _build_time_entry_group(self):
        group = QWidget()
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)

        slider_row = QHBoxLayout()
        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setMinimum(0)
        self.time_slider.setMaximum(self._TIME_SLIDER_MAX_MINUTES)
        self.time_slider.setSingleStep(self._TIME_SLIDER_STEP_MINUTES)
        self.time_slider.setPageStep(self._TIME_SLIDER_STEP_MINUTES * 2)
        self.time_slider.setToolTip(
            "Hours worked on this task today (0-12h) — auto-advances from "
            "when the task was detected until you touch the slider, a "
            "preset button, or log time"
        )
        self.time_slider.valueChanged.connect(self._on_time_slider_changed)
        # actionTriggered fires only for user-driven changes (drag,
        # arrow/page keys, click-to-page) — never for our own
        # setValue() calls — so this is what turns off auto-tracking
        # the moment the user actually touches the slider themselves.
        self.time_slider.actionTriggered.connect(self._on_time_slider_user_interacted)
        slider_row.addWidget(self.time_slider)

        self.time_slider_value_label = QLabel("0h 0m")
        self.time_slider_value_label.setMinimumWidth(72)
        slider_row.addWidget(self.time_slider_value_label)
        layout.addLayout(slider_row)

        presets_row = QHBoxLayout()
        for minutes in self._TIME_PRESETS_MINUTES:
            button = QPushButton(self._format_minutes(minutes))
            button.clicked.connect(
                lambda _checked=False, m=minutes: self._apply_time_preset(m)
            )
            presets_row.addWidget(button)
        layout.addLayout(presets_row)

        self.submit_time_button = QPushButton("Log Time")
        self.submit_time_button.setToolTip(
            "Set today's total logged time on this task to the value above "
            "(replaces, rather than adds to, any time already logged today)"
        )
        self.submit_time_button.clicked.connect(self._on_submit_time_spent_clicked)
        layout.addWidget(self.submit_time_button)

        return group

    def _format_minutes(self, minutes):
        hours, remaining_minutes = divmod(int(minutes), 60)
        return f"{hours}h {remaining_minutes}m"

    def _on_time_slider_changed(self, minutes):
        suffix = " (auto)" if self._time_tracking_active else ""
        self.time_slider_value_label.setText(f"{self._format_minutes(minutes)}{suffix}")

    def _on_time_slider_user_interacted(self, _action):
        # The user just dragged/keyed the slider themselves — stop
        # auto-advancing so it doesn't fight their manual choice; it
        # resumes on the next task (re-)detection or a successful log.
        # actionTriggered fires *before* Qt applies the action's value
        # change, so the label refresh (to drop the "(auto)" suffix) is
        # deferred to right after — otherwise a user action that doesn't
        # actually change the value (e.g. clicking exactly on the current
        # position) would leave the stale "(auto)" suffix showing
        # indefinitely, since no valueChanged would follow to refresh it.
        self._time_tracking_active = False
        QTimer.singleShot(0, lambda: self._on_time_slider_changed(self.time_slider.value()))

    def _apply_time_preset(self, minutes):
        # A preset button is just as much a manual override as dragging
        # the slider — setValue() alone wouldn't turn tracking off (it
        # doesn't fire actionTriggered), so this does it explicitly. The
        # label refresh below is unconditional (not left to valueChanged)
        # since setValue() is a no-op — no signal at all — if the preset
        # happens to match the slider's current value already.
        self._time_tracking_active = False
        self.time_slider.setValue(minutes)
        self._on_time_slider_changed(self.time_slider.value())

    def _start_time_tracking(self, base_minutes):
        """(Re)start auto-advancing the slider from `base_minutes` (the
        already-logged time as of now) — called on every task
        (re-)detection and after every successful time log, so the slider
        always resumes counting up from the latest known-good total.
        Sets the slider to `base_minutes` itself (capped to its 12h
        range), so callers don't need their own separate setValue step."""
        self._time_tracking_active = True
        self._time_tracking_base_minutes = base_minutes
        self._time_tracking_start = time.monotonic()
        if not self._time_tracking_timer.isActive():
            self._time_tracking_timer.start()

        self.time_slider.blockSignals(True)
        self.time_slider.setValue(min(int(base_minutes), self._TIME_SLIDER_MAX_MINUTES))
        self.time_slider.blockSignals(False)
        self._on_time_slider_changed(self.time_slider.value())

    def _on_time_tracking_tick(self):
        if not self._time_tracking_active:
            return
        elapsed_minutes = int((time.monotonic() - self._time_tracking_start) // 60)
        minutes = min(
            self._time_tracking_base_minutes + elapsed_minutes,
            self._TIME_SLIDER_MAX_MINUTES,
        )
        self.time_slider.setValue(minutes)

    def _on_submit_time_spent_clicked(self):
        if not (self.session and self.task):
            QMessageBox.warning(self, "Kitsu", "Detect a task before logging time.")
            return

        # The slider itself only snaps to _TIME_SLIDER_STEP_MINUTES when
        # dragged by hand -- the auto-advance timer sets it minute-by-minute
        # (see _on_time_tracking_tick), so a Log Time click mid-tick could
        # otherwise submit an odd value like 80, which Kitsu's web
        # timesheet then shows as a repeating decimal ("1.3333333h").
        step = self._TIME_SLIDER_STEP_MINUTES
        minutes = round(self.time_slider.value() / step) * step
        try:
            user = self.session.current_user()
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Could not resolve the logged-in user: {exc}")
            return

        today = date.today().isoformat()
        try:
            self.session.set_time_spent(self.task, user, today, minutes)
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Could not log time: {exc}")
            return

        self._set_status(f"Logged {self._format_minutes(minutes)} for today.")
        self._update_time_spent_today(self.task)

    def _build_feedback_group(self):
        group = QWidget()
        layout = QVBoxLayout(group)

        self.comments_list = QListWidget()
        self.comments_list.currentItemChanged.connect(self._on_comment_selected)
        layout.addWidget(self.comments_list)

        self.comment_detail_area = QScrollArea()
        self.comment_detail_area.setWidgetResizable(True)
        self.comment_detail_area.setMinimumHeight(160)
        self.comment_detail_area.setVisible(False)
        self.comment_detail_widget = QWidget()
        self.comment_detail_layout = QVBoxLayout(self.comment_detail_widget)
        self.comment_detail_layout.setAlignment(Qt.AlignTop)
        self.comment_detail_area.setWidget(self.comment_detail_widget)
        layout.addWidget(self.comment_detail_area)

        self.download_attachment_button = QPushButton("Save Attachment(s)")
        self.download_attachment_button.setToolTip(
            "Save attachment(s) from the selected comment to disk"
        )
        self.download_attachment_button.clicked.connect(
            self._on_download_attachment_clicked
        )
        layout.addWidget(self.download_attachment_button)
        return group

    def _build_publish_group(self):
        group = QWidget()
        layout = QVBoxLayout(group)

        self.status_combo = QComboBox()
        layout.addWidget(self.status_combo)

        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("Comment")
        layout.addWidget(self.comment_edit)

        layout.addWidget(self._section_label("Preview Image(s)"))
        self.variations_list = QListWidget()
        self.variations_list.setToolTip(
            "Images to publish with this update, queued explicitly here — "
            "nothing is captured automatically. The first image becomes "
            "the main preview; any additional ones are added as "
            "variations on the SAME revision (e.g. different render "
            "layers). If this list is empty, Publish just posts the "
            "status change and comment below with no preview attached."
        )
        self.variations_list.setMaximumHeight(80)
        layout.addWidget(self.variations_list)

        variations_buttons_row = QHBoxLayout()
        self.add_variation_from_viewport_button = QPushButton("+ From Viewport")
        self.add_variation_from_viewport_button.setToolTip(
            "Capture the active viewport right now and queue it as an "
            "image to publish"
        )
        self.add_variation_from_viewport_button.clicked.connect(
            self._on_add_variation_from_viewport_clicked
        )
        variations_buttons_row.addWidget(self.add_variation_from_viewport_button)

        self.add_variation_from_file_button = QPushButton("+ From File...")
        self.add_variation_from_file_button.setToolTip(
            "Browse for an existing image on disk to queue as an image "
            "to publish"
        )
        self.add_variation_from_file_button.clicked.connect(
            self._on_add_variation_from_file_clicked
        )
        variations_buttons_row.addWidget(self.add_variation_from_file_button)

        self.remove_variation_button = QPushButton("Remove Selected")
        self.remove_variation_button.clicked.connect(self._on_remove_variation_clicked)
        variations_buttons_row.addWidget(self.remove_variation_button)
        layout.addLayout(variations_buttons_row)

        self.publish_button = QPushButton("Publish")
        self.publish_button.setToolTip(
            "Post the status change and comment. If any image(s) are "
            "queued above, publishes them as a new revision (first = "
            "main preview, rest = variations); otherwise posts the "
            "status/comment alone, with no preview — nothing is "
            "captured automatically"
        )
        self.publish_button.clicked.connect(self._on_publish_clicked)
        layout.addWidget(self.publish_button)

        return group

    # ---- settings ----

    def _load_settings(self):
        values = settings.load()
        self.server_edit.setText(values["server_url"])
        self.email_edit.setText(values["email"])
        self.project_id_edit.setText(values["project_id"])
        self.token_edit.setText(values["api_token"])

    def _save_settings(self):
        settings.save(
            {
                "server_url": self.server_edit.text().strip(),
                "email": self.email_edit.text().strip(),
                "project_id": self.project_id_edit.text().strip(),
                "api_token": self.token_edit.text().strip(),
            }
        )

    # ---- status helper ----

    def _set_status(self, message, is_error=False):
        self.status_label.setText(message)
        color = "#b00020" if is_error else "#0b7a0b"
        self.status_label.setStyleSheet(f"color: {color};")

    # ---- actions ----

    def _on_login_clicked(self):
        server_url = self.server_edit.text().strip()
        email = self.email_edit.text().strip()
        password = self.password_edit.text()
        project_id = self.project_id_edit.text().strip()
        token = self.token_edit.text().strip()

        if not server_url or not project_id:
            QMessageBox.warning(self, "Kitsu", "Please fill in the server URL and project ID.")
            return
        if not token and not (email and password):
            QMessageBox.warning(self, "Kitsu", "Please enter an email/password or an API token.")
            return

        self._set_status("Connecting...")
        try:
            session = KitsuSession(server_url)
            if token:
                session.login_with_token(token)
            else:
                session.login(email, password)
            project = session.project(project_id)
        except KitsuConnectionError as exc:
            self._set_status(str(exc), is_error=True)
            return
        except Exception as exc:
            self._set_status(f"Unexpected error: {exc}", is_error=True)
            return

        self.session = session
        self.project = project
        self.password_edit.clear()

        if not token:
            # Just logged in with email/password -- save the refresh token
            # gazu obtained so the NEXT login can skip the password.
            refresh_token = session.get_refresh_token()
            if refresh_token:
                self.token_edit.setText(refresh_token)

        self._save_settings()
        project_name = project.get("name", project_id)
        self._load_project_header(project)
        self._load_user_header(session)
        self._set_status(f"Connected to {session.host} — project '{project_name}'")
        self._populate_statuses()
        self._on_refresh_task_clicked()

    _USER_THUMBNAIL_WIDTH = 80

    def _load_user_header(self, session):
        # Fetching the logged-in user's own name/avatar is a nice-to-have,
        # not something a login should fail over — any problem here just
        # leaves the greeting hidden, same as a project with no thumbnail.
        # It doubles as the one place the logged-in account is resolved:
        # the timesheet needs the person id to read back today's total.
        try:
            user = session.current_user()
        except Exception:
            self.current_user = None
            self.user_welcome_label.setVisible(False)
            self.user_thumbnail_label.setVisible(False)
            return

        self.current_user = user
        display_name = user.get("full_name") or user.get("email") or "there"
        self.user_welcome_label.setText(f"Welcome, {display_name}")
        self.user_welcome_label.setVisible(True)

        try:
            avatar_path = self._ensure_user_avatar_downloaded(session, user)
        except Exception:
            # Not every account has an avatar set on Kitsu.
            self.user_thumbnail_label.setVisible(False)
            return

        pixmap = QPixmap(avatar_path)
        if pixmap.isNull():
            self.user_thumbnail_label.setVisible(False)
            return
        if pixmap.width() > self._USER_THUMBNAIL_WIDTH:
            pixmap = pixmap.scaledToWidth(
                self._USER_THUMBNAIL_WIDTH, Qt.SmoothTransformation
            )
        self.user_thumbnail_label.setPixmap(pixmap)
        self.user_thumbnail_label.setVisible(True)

    def _ensure_user_avatar_downloaded(self, session, user):
        user_id = user.get("id")
        cache_key = f"{user_id}_user_avatar"
        if cache_key in self._preview_cache:
            cached_path = self._preview_cache[cache_key]
            if os.path.exists(cached_path):
                return cached_path

        cache_dir = os.path.join(tempfile.gettempdir(), "kitsu_previews")
        os.makedirs(cache_dir, exist_ok=True)
        target_path = os.path.join(cache_dir, f"{user_id}_user_avatar.png")

        session.download_person_avatar(user, target_path)
        self._preview_cache[cache_key] = target_path
        return target_path

    def _load_project_header(self, project):
        self.project_name_label.setText(project.get("name", ""))

        try:
            thumbnail_path = self._ensure_project_thumbnail_downloaded(project)
        except Exception:
            # Not every project has a thumbnail set on Kitsu — that's a
            # normal, silent case, not a login failure worth surfacing.
            self.project_thumbnail_label.setVisible(False)
            return

        pixmap = QPixmap(thumbnail_path)
        if pixmap.isNull():
            self.project_thumbnail_label.setVisible(False)
            return
        if pixmap.width() > self._PROJECT_THUMBNAIL_WIDTH:
            pixmap = pixmap.scaledToWidth(
                self._PROJECT_THUMBNAIL_WIDTH, Qt.SmoothTransformation
            )
        self.project_thumbnail_label.setPixmap(pixmap)
        self.project_thumbnail_label.setVisible(True)

    def _ensure_project_thumbnail_downloaded(self, project):
        project_id = project.get("id")
        cache_key = f"{project_id}_project_thumbnail"
        if cache_key in self._preview_cache:
            cached_path = self._preview_cache[cache_key]
            if os.path.exists(cached_path):
                return cached_path

        cache_dir = os.path.join(tempfile.gettempdir(), "kitsu_previews")
        os.makedirs(cache_dir, exist_ok=True)
        target_path = os.path.join(cache_dir, f"{project_id}_project_thumbnail.png")

        self.session.download_project_thumbnail(project, target_path)
        self._preview_cache[cache_key] = target_path
        return target_path

    def _on_project_id_changed(self, _text):
        # The resolved name/thumbnail only apply to the ID that was
        # actually logged in with — clear them so stale project info never
        # lingers after the ID is edited to something else.
        self.project_name_label.setText("")
        self.project_thumbnail_label.setVisible(False)

    def _populate_statuses(self):
        self.status_combo.clear()
        if not (self.session and self.project):
            return
        try:
            statuses = self.session.all_task_statuses(self.project)
        except Exception as exc:
            self._set_status(f"Could not load task statuses: {exc}", is_error=True)
            return
        for status in statuses:
            self.status_combo.addItem(status.get("name", status.get("short_name", "?")), status)

    def _on_refresh_task_clicked(self):
        if not (self.session and self.project):
            QMessageBox.warning(self, "Kitsu", "Log in first.")
            return

        previous_task_id = self.task.get("id") if self.task else None
        scene_path = maya_scene.scene_path()
        self._scene_path_for_override = scene_path
        self._hide_task_type_override()

        try:
            task, breadcrumb, context = detect_task(self.session, self.project, scene_path)
        except TaskTypeMismatch as exc:
            self._handle_task_type_mismatch(exc, scene_path, previous_task_id)
            return
        except TaskDetectionError as exc:
            self.task = None
            self.task_breadcrumb = None
            self.task_context = None
            self.task_label.setText(str(exc))
            self.task_status_label.setVisible(False)
            self.task_due_date_label.setVisible(False)
            self.task_time_spent_label.setVisible(False)
            self.time_entry_group.setVisible(False)
            self._time_tracking_active = False
            return

        self._apply_resolved_task(task, breadcrumb, context, previous_task_id)

    def _apply_resolved_task(self, task, breadcrumb, context, previous_task_id):
        self._hide_task_type_override()
        self.task = task
        self.task_breadcrumb = breadcrumb
        self.task_context = context
        self.task_label.setText(" / ".join(breadcrumb))

        # Re-detecting the SAME task (e.g. just clicking "Detect Task"
        # again, or silently re-resolving a remembered task-type
        # override) shouldn't interrupt an already-running auto-tracker or
        # discard a manual slider override -- only a genuine task change
        # (or the very first detection) restarts the timesheet tracker.
        same_task_as_before = previous_task_id is not None and previous_task_id == task.get("id")
        self._update_task_details(task, restart_time_tracking=not same_task_as_before)
        self._load_comments()

    def _hide_task_type_override(self):
        self.task_type_override_label.setVisible(False)
        self.task_type_override_combo.setVisible(False)
        self.task_type_override_combo.blockSignals(True)
        self.task_type_override_combo.clear()
        self.task_type_override_combo.blockSignals(False)
        self._pending_override_entity = None
        self._pending_override_breadcrumb = None
        self._pending_override_context = None
        self._pending_override_previous_task_id = None

    def _handle_task_type_mismatch(self, exc, scene_path, previous_task_id):
        # `detect_task` already picked asset vs. shot context up front (see
        # `_path_is_shot_context`) before it ever got as far as the
        # task-type mismatch -- re-deriving it here from the same scene
        # path keeps `self.task_context` accurate for whichever entity the
        # override ends up resolving against.
        context = "shot" if _path_is_shot_context(scene_path) else "asset"

        remembered_id = settings.load_task_type_override(scene_path) if scene_path else None
        if remembered_id:
            task_type = next(
                (
                    t
                    for t in self.session.all_task_types_for_project(self.project)
                    if t.get("id") == remembered_id
                ),
                None,
            )
            if task_type:
                task = self.session.get_task_by_entity(exc.entity, task_type)
                if task:
                    self._apply_resolved_task(
                        task,
                        exc.breadcrumb + [task_type.get("name", "?")],
                        context,
                        previous_task_id,
                    )
                    return

        self.task = None
        self.task_breadcrumb = None
        self.task_context = None
        self.task_status_label.setVisible(False)
        self.task_due_date_label.setVisible(False)
        self.task_time_spent_label.setVisible(False)
        self.time_entry_group.setVisible(False)
        self._time_tracking_active = False

        self._pending_override_entity = exc.entity
        self._pending_override_breadcrumb = exc.breadcrumb
        self._pending_override_context = context
        self._pending_override_previous_task_id = previous_task_id

        self.task_label.setText(
            " / ".join(exc.breadcrumb) + " — task type not recognized"
        )
        self.task_type_override_label.setText(
            f"Folder task type {exc.task_type_name!r} didn't match any task "
            "type in this project — pick one:"
        )
        self.task_type_override_label.setVisible(True)

        self.task_type_override_combo.blockSignals(True)
        self.task_type_override_combo.addItem("Select task type…", None)
        for task_type in self.session.all_task_types_for_project(self.project):
            self.task_type_override_combo.addItem(
                task_type.get("name", "?"), task_type
            )
        self.task_type_override_combo.setCurrentIndex(0)
        self.task_type_override_combo.blockSignals(False)
        self.task_type_override_combo.setVisible(True)

    def _on_task_type_override_changed(self, _index):
        task_type = self.task_type_override_combo.currentData()
        if task_type is None or self._pending_override_entity is None:
            return

        task = self.session.get_task_by_entity(self._pending_override_entity, task_type)
        if task is None:
            QMessageBox.warning(
                self,
                "Kitsu",
                f"{task_type.get('name', '?')!r} is a valid task type, but this "
                "entity doesn't have a task of that type yet in Kitsu.",
            )
            return

        scene_path = self._scene_path_for_override
        if scene_path:
            settings.save_task_type_override(scene_path, task_type.get("id"))

        self._apply_resolved_task(
            task,
            self._pending_override_breadcrumb + [task_type.get("name", "?")],
            self._pending_override_context,
            self._pending_override_previous_task_id,
        )

    def _update_task_details(self, task, restart_time_tracking=True):
        status_name = self._resolve_task_status_name(task)
        self.task_status_label.setText(f"Status: {status_name}")
        self.task_status_label.setVisible(True)

        self.task_due_date_label.setText(f"Due Date: {self._format_due_date(task)}")
        self.task_due_date_label.setVisible(True)

        self.time_entry_group.setVisible(True)
        self._update_time_spent_today(task, restart_time_tracking=restart_time_tracking)

    def _resolve_task_status_name(self, task):
        # Some Zou versions embed the full status object on the task
        # ("task_status"); others only give the id, resolved here against
        # the same status list already fetched for the Publish dropdown
        # — falling back to every status in the database if not found
        # there, since a project's own configured status list doesn't
        # necessarily include default statuses like "Todo" (the one
        # every new task starts in automatically, before anyone has
        # published anything), which is exactly why that fallback is
        # tried second rather than first: it's an extra network call,
        # only worth making when the cheaper per-project list comes up
        # empty.
        status = task.get("task_status") or {}
        name = status.get("name") or status.get("short_name")
        if name:
            return name

        status_id = task.get("task_status_id")
        if not status_id:
            return "Unknown"

        status_lookups = (
            lambda: self.session.all_task_statuses(self.project),
            lambda: self.session.all_task_statuses_global(),
        )
        for get_statuses in status_lookups:
            try:
                statuses = get_statuses()
            except Exception:
                continue
            for candidate in statuses:
                if candidate.get("id") == status_id:
                    return candidate.get("name") or candidate.get("short_name") or "Unknown"

        return "Unknown"

    def _format_due_date(self, task):
        value = task.get("due_date")
        if not value:
            return "Not set"
        # Zou returns an ISO date or datetime string ("2026-08-01" or
        # "2026-08-01T00:00:00") -- only the date part is relevant here.
        return str(value).split("T")[0]

    def _update_time_spent_today(self, task, restart_time_tracking=True):
        today = date.today().isoformat()
        person_id = (self.current_user or {}).get("id")
        if not person_id:
            # Without knowing which account we are, there's no way to tell
            # this user's own logged time apart from a colleague's on the
            # same task — better to say so than to show someone else's.
            self.task_time_spent_label.setText(
                "Time logged today: unknown (the logged-in account could "
                "not be resolved)"
            )
            self.task_time_spent_label.setVisible(True)
            return

        try:
            minutes = self.session.get_time_spent_today(task, today, person_id)
        except Exception as exc:
            self.task_time_spent_label.setText(f"Time logged today: could not load ({exc})")
            self.task_time_spent_label.setVisible(True)
            return

        self.task_time_spent_label.setText(f"Time logged today: {self._format_minutes(minutes)}")
        self.task_time_spent_label.setVisible(True)

        if not restart_time_tracking:
            # Re-detecting the same task: the read-only line above still
            # refreshes from the server, but the slider/timer are left
            # exactly as they are, whether that's an already-running
            # auto-tracker or a manual value the user set on purpose.
            return

        # Sync the slider to whatever's already logged today and resume
        # auto-advancing from it — covers both a fresh task detection and
        # the resync right after a successful time log, so the slider
        # keeps counting up from whatever was just confirmed rather than
        # staying frozen or resetting to 0.
        self._start_time_tracking(int(minutes))

    def _load_comments(self):
        """Full manual refresh — Detect Task, or right after publishing/
        editing something. Always rebuilds and drops any selection."""
        self._clear_attachment_preview()
        self.comment_detail_area.setVisible(False)
        if not (self.session and self.task):
            self.comments_list.clear()
            self._last_comment_ids = None
            return
        try:
            comments = self.session.all_comments_for_task(self.task)
        except Exception as exc:
            self._set_status(f"Could not load comments: {exc}", is_error=True)
            return
        self._render_comments(comments)

    def _build_comment_item(self, comment):
        revision_label = self._resolve_comment_revision(comment)
        status_label = self._resolve_comment_status(comment)
        person = self._resolve_comment_author(comment)
        text = comment.get("text", "")
        attachments = comment.get("attachment_files") or []

        prefix_parts = [part for part in (revision_label, status_label) if part]
        prefix = f"{' - '.join(prefix_parts)} - " if prefix_parts else ""
        label = f"{prefix}{person}: {text}"
        if attachments:
            label += f"  [{len(attachments)} attachment(s)]"
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, comment)
        return item

    def _render_comments(self, comments, preserve_selection=False):
        previously_selected_id = None
        if preserve_selection:
            current_item = self.comments_list.currentItem()
            if current_item is not None:
                previously_selected_id = current_item.data(Qt.UserRole).get("id")

        self.comments_list.clear()
        for comment in comments:
            item = self._build_comment_item(comment)
            self.comments_list.addItem(item)
            if previously_selected_id is not None and comment.get("id") == previously_selected_id:
                self.comments_list.setCurrentItem(item)

        self._last_comment_ids = [comment.get("id") for comment in comments]

    def _on_comment_poll_tick(self):
        # Background auto-refresh so a comment/revision someone else just
        # posted on Kitsu shows up without having to click Detect Task —
        # silent on failure (a transient network hiccup shouldn't nag the
        # user every 60s) and a no-op entirely when nothing actually
        # changed, so it never disrupts a comment you have selected/open.
        if not (self.session and self.task):
            return
        try:
            comments = self.session.all_comments_for_task(self.task)
        except Exception:
            return

        new_ids = [comment.get("id") for comment in comments]
        if new_ids == self._last_comment_ids:
            return
        self._render_comments(comments, preserve_selection=True)

    def _resolve_comment_revision(self, comment):
        # A comment has a preview attached exactly when it was posted
        # alongside a new published version (see publish_preview) — so
        # this is "did this comment introduce a new revision?", not a
        # generic preview lookup. Comments without one (a plain reply,
        # or a status-only change) get no revision prefix at all.
        previews = comment.get("previews") or []
        if not previews:
            return None
        revision = previews[0].get("revision")
        return f"Revision {revision}" if revision is not None else None

    def _resolve_comment_status(self, comment):
        # The status a comment set the task to — Zou embeds this the same
        # way it embeds "person"/"attachment_files"/"previews" on the same
        # enriched comment object. Shown as its short_name in caps (e.g.
        # "WIP"), matching Kitsu's own status-badge style; falls back to
        # the full name if a status has no short_name, and is simply
        # omitted (not shown as "None") if a comment has neither — this
        # happens for comments with no associated status change.
        status = comment.get("task_status") or {}
        label = status.get("short_name") or status.get("name")
        return label.upper() if label else None

    def _resolve_comment_author(self, comment):
        person = comment.get("person") or {}
        full_name = person.get("full_name")
        if full_name:
            return full_name

        # Kitsu can omit the nested "person" object (e.g. client-portal
        # accounts have studio identities hidden) even for a comment's own
        # author; fall back to a direct person lookup by id.
        person_id = comment.get("person_id")
        if person_id and self.session:
            try:
                person = self.session.get_person(person_id)
            except Exception:
                person = None
            if person and person.get("full_name"):
                return person["full_name"]

        return "Unknown"

    # ---- comment detail (checklist + attachment preview) ----

    _IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"}
    _VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "webm", "mkv", "m4v"}

    def _clear_attachment_preview(self):
        while self.comment_detail_layout.count():
            child = self.comment_detail_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_comment_selected(self, current, previous):
        self._clear_attachment_preview()
        if current is None:
            self.comment_detail_area.setVisible(False)
            return

        comment = current.data(Qt.UserRole)

        checklist = comment.get("checklist") or []
        if checklist:
            self.comment_detail_layout.addWidget(self._section_label("Checklist"))
            for index, checklist_item in enumerate(checklist):
                checkbox = QCheckBox(checklist_item.get("text", ""))
                checkbox.setChecked(bool(checklist_item.get("checked")))
                checkbox.stateChanged.connect(
                    lambda _state, c=comment, i=index, cb=checkbox: self._on_checklist_item_toggled(c, i, cb)
                )
                self.comment_detail_layout.addWidget(checkbox)

        previews = comment.get("previews") or []
        if previews:
            self.comment_detail_layout.addWidget(self._section_label("Revisions"))
            for preview in previews:
                self.comment_detail_layout.addWidget(self._build_revision_preview(preview))

        attachments = comment.get("attachment_files") or []
        if attachments:
            self.comment_detail_layout.addWidget(self._section_label("Attachments"))
            for attachment in attachments:
                self.comment_detail_layout.addWidget(self._build_attachment_preview(attachment))

        # Hidden entirely for a comment with nothing to show, rather than
        # leaving an empty 160px-tall box below the list -- this is the
        # only reason `has_content` is tracked at all.
        has_content = bool(checklist or previews or attachments)
        self.comment_detail_area.setVisible(has_content)
        if not has_content:
            return

        # Without this, the scroll area's minimum height stretches the
        # content widget taller than the actual checklist/attachments, and
        # the layout distributes that leftover space between items instead
        # of pushing it below everything — showing up as a gap right under
        # the "Checklist" title.
        self.comment_detail_layout.addStretch(1)

    def _section_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold;")
        return label

    def _on_checklist_item_toggled(self, comment, index, checkbox):
        checklist = comment.get("checklist") or []
        previous_value = checklist[index].get("checked", False)
        new_value = checkbox.isChecked()
        checklist[index]["checked"] = new_value

        try:
            self.session.update_comment_checklist(comment["id"], checklist)
        except Exception as exc:
            checklist[index]["checked"] = previous_value
            checkbox.blockSignals(True)
            checkbox.setChecked(previous_value)
            checkbox.blockSignals(False)
            QMessageBox.warning(self, "Kitsu", f"Could not sync checklist item to Kitsu: {exc}")

    def _wrapped_label(self, text):
        # Exception messages/filenames can be long — without word-wrap, a
        # single-line QLabel's minimum width would be the whole message,
        # blocking the panel from narrowing whenever one is shown.
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _build_attachment_preview(self, attachment):
        file_name = attachment.get("name") or str(attachment.get("id"))
        extension = (attachment.get("extension") or os.path.splitext(file_name)[1].lstrip(".")).lower()

        try:
            local_path = self._ensure_attachment_downloaded(attachment)
        except Exception as exc:
            return self._wrapped_label(f"{file_name}: could not download ({exc})")

        if extension in self._IMAGE_EXTENSIONS:
            pixmap = QPixmap(local_path)
            if not pixmap.isNull():
                label = QLabel()
                label.setPixmap(
                    pixmap.scaledToWidth(320, Qt.SmoothTransformation)
                    if pixmap.width() > 320
                    else pixmap
                )
                label.setToolTip(file_name)
                return label

        return self._wrapped_label(f"{file_name} (no preview available for this file type)")

    def _build_revision_preview(self, preview):
        revision = preview.get("revision", "?")
        file_name = preview.get("original_name") or f"revision_{revision}"

        extension = (preview.get("extension") or "").lower()
        if extension in self._VIDEO_EXTENSIONS:
            return self._build_video_revision_preview(preview)

        try:
            local_path = self._ensure_preview_downloaded(preview)
        except Exception as exc:
            return self._wrapped_label(f"Revision {revision}: could not download ({exc})")

        pixmap = QPixmap(local_path)
        if pixmap.isNull():
            return self._wrapped_label(f"Revision {revision} (no preview available for this file type)")

        objects = picture_annotation_objects(preview)
        has_drawover = bool(objects)
        if has_drawover:
            pixmap = render_annotations(pixmap, objects, preview.get("width"), preview.get("height"))

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        caption = f"Revision {revision}" + (" (with drawover)" if has_drawover else "")
        container_layout.addWidget(QLabel(caption))

        if has_drawover:
            plane_button = QPushButton("Add as Image Plane")
            plane_button.setToolTip(
                "Show this revision with its drawover as an image plane on "
                "the active viewport's camera"
            )
            plane_button.clicked.connect(
                lambda _checked=False, p=preview, px=pixmap: self._on_add_drawover_image_plane_clicked(p, px)
            )
            container_layout.addWidget(plane_button)

        image_label = QLabel()
        image_label.setPixmap(
            pixmap.scaledToWidth(320, Qt.SmoothTransformation) if pixmap.width() > 320 else pixmap
        )
        image_label.setToolTip(file_name)
        container_layout.addWidget(image_label)

        return container

    # ---- video revisions (frame-based feedback) ----

    def _project_fps(self):
        """The production's fps (from Kitsu), falling back to the scene's."""
        fps = 0.0
        if self.project:
            try:
                fps = float(self.project.get("fps") or 0)
            except (TypeError, ValueError):
                fps = 0.0
        return fps if fps > 0 else maya_scene.scene_fps()

    def _build_video_revision_preview(self, preview):
        revision = preview.get("revision", "?")
        frames = video_annotations.annotated_frames(preview, self._project_fps())

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        caption = f"Revision {revision} (video"
        caption += f", {len(frames)} annotated frame(s))" if frames else ")"
        container_layout.addWidget(QLabel(caption))

        if frames:
            frame_list = ", ".join(str(f) for f in sorted(frames))
            container_layout.addWidget(
                self._wrapped_label(f"Feedback on frame(s): {frame_list}")
            )

            markers_button = QPushButton("Add Timeline Markers")
            markers_button.setToolTip(
                "Bookmark every frame that has review markup on the time "
                "slider (video frame 0 = your playback start frame)"
            )
            markers_button.clicked.connect(
                lambda _checked=False, p=preview, f=frames: self._on_add_timeline_markers_clicked(p, f)
            )
            container_layout.addWidget(markers_button)

            drawover_button = QPushButton("Show Drawovers in Viewport")
            drawover_button.setToolTip(
                "Attach an image plane to the active camera that shows each "
                "frame's drawover only on that exact frame (transparent "
                "everywhere else)"
            )
            drawover_button.clicked.connect(
                lambda _checked=False, p=preview, f=frames: self._on_show_drawovers_clicked(p, f)
            )
            container_layout.addWidget(drawover_button)

        try:
            thumbnail_path = self._ensure_thumbnail_downloaded(preview)
        except Exception as exc:
            container_layout.addWidget(
                self._wrapped_label(f"(thumbnail unavailable: {exc})")
            )
            return container

        pixmap = QPixmap(thumbnail_path)
        if not pixmap.isNull():
            thumb_label = QLabel()
            thumb_label.setPixmap(
                pixmap.scaledToWidth(320, Qt.SmoothTransformation)
                if pixmap.width() > 320
                else pixmap
            )
            thumb_label.setToolTip(preview.get("original_name") or f"revision_{revision}")
            container_layout.addWidget(thumb_label)

        return container

    def _on_add_timeline_markers_clicked(self, preview, frames):
        revision = preview.get("revision", "?")
        start = maya_scene.playback_start()
        prefix = f"Kitsu r{revision} f"
        labels = {
            start + kitsu_frame: f"{prefix}{kitsu_frame}"
            for kitsu_frame in frames
        }
        try:
            maya_scene.set_timeline_bookmarks(prefix, labels)
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Could not create timeline markers: {exc}")
            return
        self._set_status(
            f"Added {len(labels)} timeline marker(s) for revision {revision} "
            f"(video frame 0 = frame {start})."
        )

    def _on_show_drawovers_clicked(self, preview, frames):
        revision = preview.get("revision", "?")
        start = maya_scene.playback_start()

        # Target size for the drawover images: the video's own pixel size
        # when Kitsu provides it, else the scene's render resolution (the
        # published playblast shares its aspect, so the contain-fit
        # mapping in annotations.py lines up either way).
        width = preview.get("width")
        height = preview.get("height")
        if not (width and height):
            width, height = maya_scene.render_resolution()

        sequence_dir = os.path.join(
            tempfile.gettempdir(),
            "kitsu_previews",
            f"{preview.get('id')}_drawover_seq",
        )
        os.makedirs(sequence_dir, exist_ok=True)

        maya_frames = []
        first_file = None
        for kitsu_frame, objects in sorted(frames.items()):
            maya_frame = start + kitsu_frame
            base = QPixmap(width, height)
            base.fill(Qt.transparent)
            composited = render_annotations(base, objects, width, height)
            file_path = os.path.join(sequence_dir, f"drawover.{maya_frame:04d}.png")
            if not composited.save(file_path, "PNG"):
                QMessageBox.warning(self, "Kitsu", f"Could not write {file_path}.")
                return
            maya_frames.append(maya_frame)
            if first_file is None:
                first_file = file_path

        blank_frame = max(maya_frames) + 1
        blank = QPixmap(width, height)
        blank.fill(Qt.transparent)
        blank_path = os.path.join(sequence_dir, f"drawover.{blank_frame:04d}.png")
        if not blank.save(blank_path, "PNG"):
            QMessageBox.warning(self, "Kitsu", f"Could not write {blank_path}.")
            return

        plane_name = f"Feedback_{revision}"
        try:
            maya_scene.set_feedback_drawover_sequence(
                first_file, maya_frames, blank_frame, plane_name
            )
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Could not create the image plane: {exc}")
            return

        self._set_status(
            f"Added '{plane_name}' image plane — drawovers show on "
            f"{len(maya_frames)} frame(s), video frame 0 = frame {start}."
        )

    def _ensure_thumbnail_downloaded(self, preview):
        preview_id = preview.get("id")
        cache_key = f"{preview_id}_thumbnail"
        if cache_key in self._preview_cache:
            cached_path = self._preview_cache[cache_key]
            if os.path.exists(cached_path):
                return cached_path

        cache_dir = os.path.join(tempfile.gettempdir(), "kitsu_previews")
        os.makedirs(cache_dir, exist_ok=True)
        target_path = os.path.join(cache_dir, f"{preview_id}_thumbnail.png")

        self.session.download_preview_thumbnail(preview, target_path)
        self._preview_cache[cache_key] = target_path
        return target_path

    def _on_add_drawover_image_plane_clicked(self, preview, composited_pixmap):
        # Unlike Krita (where the drawover alone goes on a transparent
        # paint layer over the artist's own canvas), a 3D viewport has
        # nothing for a bare drawover to align to — so the image plane
        # shows the full revision image with the drawover composited on
        # top, which is what the reviewer was actually looking at.
        revision = preview.get("revision", "?")
        image_path = os.path.join(
            tempfile.gettempdir(),
            "kitsu_previews",
            f"{preview.get('id')}_drawover.png",
        )
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        if not composited_pixmap.save(image_path, "PNG"):
            QMessageBox.warning(self, "Kitsu", "Could not write the drawover image to disk.")
            return

        plane_name = f"Feedback_{revision}"
        try:
            maya_scene.set_feedback_image_plane(image_path, plane_name)
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Could not create the image plane: {exc}")
            return

        self._set_status(f"Added '{plane_name}' image plane with the drawover.")

    def _ensure_preview_downloaded(self, preview):
        preview_id = preview.get("id")
        if preview_id in self._preview_cache:
            cached_path = self._preview_cache[preview_id]
            if os.path.exists(cached_path):
                return cached_path

        cache_dir = os.path.join(tempfile.gettempdir(), "kitsu_previews")
        os.makedirs(cache_dir, exist_ok=True)

        name = preview.get("original_name")
        if not (name and "." in name):
            name = f"{preview_id}.{preview.get('extension') or 'png'}"
        target_path = os.path.join(cache_dir, f"{preview_id}_{name}")

        self.session.download_preview(preview, target_path)
        self._preview_cache[preview_id] = target_path
        return target_path

    def _ensure_attachment_downloaded(self, attachment):
        attachment_id = attachment.get("id")
        if attachment_id in self._attachment_cache:
            cached_path = self._attachment_cache[attachment_id]
            if os.path.exists(cached_path):
                return cached_path

        cache_dir = os.path.join(tempfile.gettempdir(), "kitsu_attachments")
        os.makedirs(cache_dir, exist_ok=True)

        file_name = attachment.get("name") or str(attachment_id)
        target_path = os.path.join(cache_dir, f"{attachment_id}_{file_name}")

        self.session.download_attachment(attachment, target_path)
        self._attachment_cache[attachment_id] = target_path
        return target_path

    def _on_download_attachment_clicked(self):
        item = self.comments_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Kitsu", "Select a comment first.")
            return

        comment = item.data(Qt.UserRole)
        attachments = comment.get("attachment_files") or []
        if not attachments:
            QMessageBox.information(self, "Kitsu", "This comment has no attachments.")
            return

        saved_paths = []
        for attachment in attachments:
            file_name = attachment.get("name") or str(attachment.get("id"))
            try:
                saved_paths.append(self._ensure_attachment_downloaded(attachment))
            except Exception as exc:
                QMessageBox.warning(self, "Kitsu", f"Could not download {file_name}: {exc}")

        if saved_paths:
            QMessageBox.information(self, "Kitsu", "Saved to:\n" + "\n".join(saved_paths))

    # ---- publish variations (multiple images on the SAME revision) ----

    _VARIATION_IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)"

    def _refresh_variations_list(self):
        self.variations_list.clear()
        for path in self._pending_variations:
            self.variations_list.addItem(os.path.basename(path))

    def _on_add_variation_from_viewport_clicked(self):
        scene_path = maya_scene.scene_path()
        base_name = os.path.splitext(os.path.basename(scene_path))[0] if scene_path else "kitsu_publish_preview"
        index = len(self._pending_variations) + 1
        export_path = os.path.join(
            tempfile.gettempdir(), f"{base_name}_variation_{index}.png"
        )
        try:
            maya_scene.capture_viewport(export_path)
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Viewport capture failed: {exc}")
            return

        self._pending_variations.append(export_path)
        self._refresh_variations_list()

    def _on_add_variation_from_file_clicked(self):
        file_path, _filter = QFileDialog.getOpenFileName(
            self, "Add Preview Image", "", self._VARIATION_IMAGE_FILTER
        )
        if not file_path:
            return
        self._pending_variations.append(file_path)
        self._refresh_variations_list()

    def _on_remove_variation_clicked(self):
        row = self.variations_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Kitsu", "Select an image to remove first.")
            return
        del self._pending_variations[row]
        self._refresh_variations_list()

    def _on_publish_clicked(self):
        if not (self.session and self.task):
            QMessageBox.warning(self, "Kitsu", "Detect a task before publishing.")
            return

        status_data = self.status_combo.currentData()
        if status_data is None:
            QMessageBox.warning(self, "Kitsu", "No task status selected.")
            return

        comment_text = self.comment_edit.toPlainText().strip()

        if not self._pending_variations:
            # Nothing was explicitly queued to show — post the status
            # change/comment alone, with no preview. Never fall back to
            # silently capturing the viewport: what gets shown on a task
            # is the artist's call, made explicitly via "+ From Viewport"
            # / "+ From File...", not an automatic side effect of a status
            # change like switching to WIP just to signal starting work.
            try:
                self.session.add_comment(self.task, status_data, comment=comment_text)
            except Exception as exc:
                QMessageBox.warning(self, "Kitsu", f"Publish failed: {exc}")
                return

            self.comment_edit.clear()
            self._set_status("Published (status/comment only, no preview).")
            self._load_comments()
            return

        scene_path = maya_scene.scene_path()
        revision = parse_version_number(scene_path)
        main_path, *extra_paths = self._pending_variations

        try:
            new_comment, preview_file = self.session.publish_preview(
                self.task,
                status_data,
                comment=comment_text,
                preview_file_path=main_path,
                revision=revision,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Publish failed: {exc}")
            return

        # Any further queued images become extra preview files on this
        # SAME comment/revision — not new revisions, not plain
        # attachments. Uploaded ones are removed from the queue; a failed
        # one stays queued so it's easy to retry rather than having to
        # re-add it. The main image is only dropped from the queue once
        # everything below has actually succeeded.
        uploaded_count = 0
        failed = []
        still_pending = []
        for variation_path in extra_paths:
            try:
                self.session.add_extra_preview(
                    self.task, new_comment, preview_file, variation_path
                )
                uploaded_count += 1
            except Exception as exc:
                failed.append((variation_path, exc))
                still_pending.append(variation_path)
        self._pending_variations = still_pending
        self._refresh_variations_list()

        self.comment_edit.clear()
        status_message = "Published."
        if revision is not None:
            status_message = f"Published as revision {revision}."
        if uploaded_count:
            status_message += f" Included {uploaded_count} additional image(s)."
        self._set_status(status_message)

        if failed:
            details = "\n".join(f"{os.path.basename(p)}: {exc}" for p, exc in failed)
            QMessageBox.warning(
                self,
                "Kitsu",
                "The publish succeeded, but some additional images could "
                f"not be uploaded (still queued to retry):\n{details}",
            )
        self._load_comments()


_panel = None


def show_panel():
    """Show the Kitsu panel, docked on the right, creating it if needed."""
    global _panel

    if _panel is not None:
        try:
            _panel.show(dockable=True, area="right", floating=False)
            _panel.raise_()
            return _panel
        except RuntimeError:
            # The underlying C++ widget was deleted (e.g. its workspace
            # control was closed and torn down) — rebuild from scratch.
            _panel = None

    # A workspaceControl of the same name can survive from a previous
    # session (Maya restores workspaces on startup) without our widget in
    # it; remove it so the mixin can create a fresh, populated one.
    if cmds.workspaceControl(_WORKSPACE_CONTROL, query=True, exists=True):
        cmds.deleteUI(_WORKSPACE_CONTROL, control=True)

    _panel = KitsuPanel()
    _panel.show(dockable=True, area="right", floating=False)
    return _panel


def get_active_panel():
    """Return the current KitsuPanel instance if show_panel() has created
    one and its underlying Qt widget is still alive, else None.

    Used by menu actions (e.g. Publish Capture) that need the panel's
    already-resolved login/task state without opening or touching the UI
    themselves — they should tell the user to open the panel first rather
    than creating one behind their back.
    """
    if _panel is None:
        return None
    try:
        _panel.objectName()  # cheap call that raises once the underlying
                              # C++ widget has been deleted
    except RuntimeError:
        return None
    return _panel
