# Kitsu Maya Plugin

A Maya Python plug-in that connects to a self-hosted Kitsu/Zou (CG Wire)
instance: it detects the task a scene belongs to from its folder
structure, shows feedback/comments (including drawover attachments), and
publishes new versions with a comment and status change.

This is the Maya counterpart of the
[Kitsu Krita Plugin](../kitsu-krita-plugin) — same features, same UI
layout, same Kitsu-side behavior, with the host-specific pieces swapped
for their Maya equivalents (viewport capture instead of canvas export,
image planes instead of paint layers).

All Kitsu/Zou protocol handling (auth, endpoints, uploads/downloads) is
delegated to CGWire's official [`gazu`](https://github.com/cgwire/gazu)
Python client. Maya's bundled Python (mayapy) doesn't have `gazu` or
`requests`, so both are vendored inside
`kitsu_maya_plugin/vendor/` along with `requests`'s own dependencies
(`urllib3`, `certifi`, `idna`). No manual `pip install` into Maya's
Python is required.

Works with Maya 2023–2026: the plugin auto-detects Maya's bundled Qt
binding (PySide2 up to Maya 2024, PySide6 from Maya 2025). Maya 2022 and
older are not supported — their Python 3.7 is too old for the vendored
`requests`/`gazu` versions.

## Folder layout

Everything under `modules/` is exactly what gets copied into Maya's user
modules folder — a standard [Maya module](https://help.autodesk.com/view/MAYAUL/2025/ENU/?guid=Maya_SDK_Distributing_Maya_Plug_ins_DistributingUsingModules_html):

```
modules/
  kitsu_maya_plugin.mod            # module descriptor Maya reads at startup
  kitsu_maya_plugin/               # the module
    plug-ins/
      kitsuMayaPlugin.py           # plug-in entry point: adds the "Kitsu/Zou" menu
    icons/
      kitsu_maya_plugin.png        # shelf button icon: the Kitsu fox logo (32x32, plus @2x for HiDPI)
      kitsu_maya_plugin@2x.png
    scripts/
      kitsu_maya_plugin/           # the plugin package
        __init__.py
        qt.py                      # PySide6/PySide2 binding shim
        panel.py                   # the dockable panel UI (login, task, feedback, publish)
        collapsible_section.py     # collapsible header used for each panel section
        kitsu_api.py               # wraps vendored gazu for login/tasks/comments
        task_detection.py          # resolves asset/task from folder structure
        maya_scene.py              # scene path, viewport capture, image planes, timeline bookmarks
        video_annotations.py       # per-frame drawover extraction for video previews
        shelf.py                   # optional shelf button ("Kitsu/Zou > Add Shelf Button")
        publish_capture.py         # "Kitsu/Zou > Publish Capture": full-scene video export + Kitsu upload
        capture_dialog.py          # status/comment dialog shown before a Publish Capture export
        capture_naming.py          # studio review-file naming convention for Publish Capture
        video_export.py            # PNG sequence -> H.264 MP4 via ffmpeg
        settings.py                # persists server URL / email / project id / collapsed sections
        versioning.py              # parses "_v###" filename versions
        annotations.py             # renders Kitsu drawovers (Fabric.js shapes) locally
        vendor/
          gazu/                    # vendored CGWire gazu client
          requests/, urllib3/,     # vendored gazu runtime dependencies
          certifi/, idna/
          typing_extensions.py     # shim (Maya's Python already has typing.Literal)
```

The plug-in file is named `kitsuMayaPlugin.py` (not
`kitsu_maya_plugin.py`) on purpose: Maya registers a Python plug-in in
`sys.modules` under its file name, which would otherwise collide with
the `kitsu_maya_plugin` package next to it.

## Installation

### Automatically

Run the installer script for your OS from the repository root — each
copies `modules/kitsu_maya_plugin.mod` and `modules/kitsu_maya_plugin/`
into Maya's user modules folder for you (removing any previous install of
the plugin's own folder first, so nothing stale from an older version is
left behind):

- **Windows**: double-click `install_windows.cmd` (installs to
  `%USERPROFILE%\Documents\maya\modules`).
- **macOS**: double-click `install_mac.command` (installs to
  `~/Library/Preferences/Autodesk/maya/modules`). If it won't open ("not
  executable"), run `chmod +x install_mac.command` once in Terminal
  first.
- **Linux**: run `./install_linux.sh` in a terminal (`chmod +x
  install_linux.sh` first if needed). Installs to `~/maya/modules`.

These locations are version-independent — Maya scans them at startup for
every installed Maya version, so one install covers all of them. If you
use a custom `MAYA_MODULE_PATH`, pass that modules folder as an argument
to the script instead, e.g. `install_linux.sh /path/to/your/modules`.

### Manually

1. Copy **both** `modules/kitsu_maya_plugin.mod` and the
   `modules/kitsu_maya_plugin/` folder into your Maya user modules folder
   (create it if it doesn't exist):
   - Windows: `%USERPROFILE%\Documents\maya\modules`
   - macOS: `~/Library/Preferences/Autodesk/maya/modules`
   - Linux: `~/maya/modules`
2. Restart Maya.
3. Go to **Windows → Settings/Preferences → Plug-in Manager**, find
   `kitsuMayaPlugin.py`, and check both **Loaded** and **Auto load**.
4. A **"Kitsu/Zou"** entry appears in the main menu bar, next to Help.
   Click **Kitsu/Zou → Open Kitsu Panel** to open the panel — it docks on
   the right like the Attribute Editor, and can be re-docked/tabbed
   anywhere.

### If the plug-in fails to load

Open the Script Editor (**Windows → General Editors → Script Editor**) —
the Python traceback from the failed load prints there. Since `gazu` and
`requests` (plus their dependencies) are fully vendored, the plug-in and
menu should load regardless of what's installed in Maya's Python —
importing them is deferred until you actually click **Login**, so a
problem there only fails the login attempt with a specific message, not
the whole plug-in.

If you get an error like `module 'gazu.xyz' has no attribute '...'` for
something this plugin clearly uses, it means some *other* `gazu` package
is being found before this plugin's own vendored copy — e.g. a leftover
`pip install gazu` into Maya's Python, or a studio-wide `PYTHONPATH`
entry pointing at a different version. The vendored copy is always
inserted first on `sys.path`, and every Login attempt also clears out
any *already-imported* `gazu` that didn't come from this plugin's own
`vendor/` folder — so this should self-heal on your very next Login
without needing to restart Maya. If it still doesn't, run
`import gazu; print(gazu.__file__)` in the Script Editor — if the path
shown isn't inside this plugin's own `vendor/gazu/`, that confirms
something is re-importing its own `gazu` even more aggressively (e.g. a
`userSetup.py` re-adding its path afterward) and points at what to
remove from your Python environment.

## Shelf integration

The menu is enough to use the plugin, but a shelf button is one click
closer. Click **Kitsu/Zou → Add Shelf Button** to add a "Kitsu" icon to
the currently active shelf — it opens the panel exactly like the menu
entry (loading the plug-in first if it isn't already), and clicking it
again on the same shelf replaces the old button rather than duplicating
it. The shelf itself is Maya's own — like any shelf button, it's saved
into your Maya preferences automatically when Maya exits, so it's there
on your next launch without re-running anything, and can be moved,
recolored, or dragged to another shelf like any other button.

If you'd rather build a shelf button by hand (e.g. as part of a studio
shelf deployed to everyone), here's what one needs:

- **Command** (Python): open the panel, loading the plug-in first if
  needed —
  ```python
  import maya.cmds as cmds
  if not cmds.pluginInfo("kitsuMayaPlugin", query=True, loaded=True):
      cmds.loadPlugin("kitsuMayaPlugin")
  from kitsu_maya_plugin import panel
  panel.show_panel()
  ```
- **Icon**: `icons/kitsu_maya_plugin.png` in this repo (32×32 PNG, plus a
  `@2x` variant for HiDPI displays) — pass just the file name as
  `image1`, not a full path. Maya module folders named `icons/` are
  automatically added to `XBMLANGPATH` (the same mechanism that puts
  `scripts/` on `PYTHONPATH`), so Maya resolves the name the same way it
  resolves its own built-in shelf icons, regardless of where the module
  ends up installed. A different icon can be substituted the same way —
  it just needs to be a PNG somewhere on `XBMLANGPATH`.
- **Source type**: `python` (as opposed to `mel`).

In `cmds.shelfButton(...)` terms:
```python
cmds.shelfButton(
    parent=shelf_name,       # a shelfLayout name, e.g. the active tab
    label="Kitsu",
    annotation="Open the Kitsu panel",
    image1="kitsu_maya_plugin.png",
    command=command_string_above,
    sourceType="python",
    style="iconOnly",
)
```
This is exactly what `shelf.add_shelf_button()`
([shelf.py](modules/kitsu_maya_plugin/scripts/kitsu_maya_plugin/shelf.py))
does — feel free to import and call it directly (e.g. from a studio
startup script) instead of going through the menu.

## Using the plugin

Each section (Connection, Task, Feedback, Publish) has a clickable title
that collapses/expands it — handy for reclaiming vertical space once you
don't need, say, the Connection or Task section visible anymore.
Collapsed/expanded state is remembered per section across Maya restarts.

1. Open the Kitsu panel and enter your server URL (e.g.
   `https://kitsu.yourstudio.com`), email + password (or an API token),
   and the project ID, then click **Login**.
   - The server URL is normalized to end in `/api` automatically (gazu's
     convention), matching how the official CGWire `gazu` client expects
     the host to be configured.
   - If login fails, the panel shows the *specific* reason (rejected
     credentials, a 404 route, a server error, etc.) instead of a generic
     failure — if you get a 404, it usually means the Zou API for this
     Kitsu instance lives at a different host/path than the web UI, and
     you'll need to ask whoever administers the server for the correct
     API URL.
   - Once logged in, a **"Welcome, `<your name>`"** greeting and your
     Kitsu avatar appear at the bottom of the Connection section, below
     the API token field — confirming at a glance which account you're
     connected as. Accounts without an avatar set on Kitsu just show the
     greeting.
   - Logging in with email/password automatically fills in and saves the
     **API token** field with a fresh refresh token. From then on, **every
     time you open the panel with a server URL, project ID, and that
     token already filled in, it logs in automatically** — no click
     needed at all, not even once. This token isn't your password (which
     is still never saved — see Notes on security) and it eventually
     expires on its own; if it has, the automatic attempt just fails
     quietly (shown in the status line, not a popup) and you log in with
     your password once more, which replaces it with a fresh one and
     resumes auto-login from there. There's nothing to manage by hand.
   - Once logged in, the project's **thumbnail and name** show at the top
     of the **Task** section, above the detected asset/shot breadcrumb —
     so you don't have to keep track of which hash belongs to which
     project, and glancing at the Task section alone tells you both which
     project and which task you're working on. Projects without a
     thumbnail set on Kitsu just show the name. Editing the Project ID
     field afterwards clears both until you log in again, so they never
     show stale info for a different project.
2. Open a scene and click **Detect Task** — the plugin resolves the
   Kitsu task from the scene's folder structure (folder names matched
   case-insensitively against Kitsu's names). Both **asset** and **shot**
   contexts are supported:

   | Context | Expected folder layout above the file |
   | --- | --- |
   | Asset | `.../<AssetType>/<AssetName>/<TaskType>/file.ma`<br>e.g. `assets/characters/Hero/modeling/Hero_modeling_v002.ma` |
   | Shot (movie / short / commercial) | `.../<Sequence>/<Shot>/<TaskType>/file.ma`<br>e.g. `shots/seq010/sh0100/animation/sh0100_animation_v003.ma` |
   | Shot (TV series, with episodes) | `.../<Episode>/<Sequence>/<Shot>/<TaskType>/file.ma`<br>e.g. `episodes/ep01/seq010/sh0100/animation/sh0100_animation_v003.ma` |

   The plugin decides **asset vs. shot** from an anchor folder anywhere in
   the path (case-insensitive): a folder named `assets`/`asset` means
   asset context, while `shots`/`shot`/`sequences`/`sequence`/`episodes`/
   `episode` means shot context. With no such anchor it assumes an asset
   (unchanged from before). It decides whether shots sit **under
   episodes** from the *project itself* — only a Kitsu `tvshow` production
   has episodes, so for a TV-show project the extra `<Episode>` level is
   required and for a movie/short/commercial it must be absent. The
   detected breadcrumb (e.g. `seq010 / sh0100 / animation` or
   `ep01 / seq010 / sh0100 / animation`) is shown in the Task section,
   along with the task's current **Status**, **Due Date** ("Not set" if
   Kitsu has none) — resolved against your project's own configured
   statuses first, falling back to every status in the database if not
   found there, since a project's status list doesn't always include
   Kitsu's default "Todo" status (the one every new task starts in
   automatically, before anyone's published anything) — and a
   **timesheet** for logging hours
   worked on this task today: a slider (0-12h, in half-hour steps),
   **1h / 4h / 8h** preset buttons that jump the slider straight to that
   value, and a **Log Time** button that submits it — matching Kitsu's
   own web timesheet slider. Submitting **sets** the day's total rather
   than adding to it (so it's safe to re-submit after adjusting the
   slider), and detecting a task you've already logged time on today (for
   the first time this session) starts the slider at that value instead
   of resetting to 0 — capped visually to 12h if you logged more than
   that elsewhere (e.g. on the web), with the true total still shown in
   the **Time logged today** line above the slider.

   The slider also **auto-advances** on its own: from the moment a task
   is first detected, it counts up in real time from whatever's already
   logged today, so after an hour of work it reads "1h" without you
   touching anything — click **Log Time** whenever you want to confirm
   it, or drag the slider (or click a preset) first to adjust the value,
   which pauses the auto-advance until it's restarted (see below), so it
   never fights a value you just set on purpose. While auto-advancing,
   the value label shows "(auto)" next to the time so it's clear it's
   still counting; that label switches to a plain value the moment you
   take control. This is a lightweight timer (checking every 30 seconds)
   piggybacking on Maya's own UI event loop — no separate thread, no
   measurable CPU cost.

   Clicking **Detect Task** again for the *same* task — which is the
   common case, since it's easy to click it more than once out of habit
   — leaves the tracker completely alone, running or paused, exactly as
   it was: it does **not** reset the clock or discard a manual override.
   Only a genuine change of task (a different shot/asset) or a successful
   **Log Time** restarts the tracker, in each case from that task's own
   already-logged total.

   If **Log Time** fails, the message explains why (no time-tracking
   route on this server, no permission to log time here, or a session
   that couldn't refresh itself — each says so explicitly). If you ever
   see a bare, unexplained URL as the entire error message instead, that
   means an unanticipated failure slipped through unmet — everything
   Log Time itself can hit should already come with a plain-English
   reason.
3. Existing comments for that task show up in the **Feedback** section,
   each listed as `<STATUS> - <name>: <comment text>` — e.g.
   `WIP - Jane Doe: first initial test` — showing the status the comment
   set the task to (in caps, matching Kitsu's own status-badge style) and
   who wrote it. Older comments with no associated status just show
   `<name>: <comment text>`. If the comment introduced a **new published
   revision** (i.e. it has a preview attached, the same one shown below
   when you select it), it's prefixed with that revision number too:
   `Revision 5 - WIP - Jane Doe: first initial test`.

   The list also **auto-refreshes in the background** every 60 seconds
   while a task is detected, so a new comment or revision someone else
   posts on Kitsu shows up without clicking Detect Task — but only when
   something actually changed: if nothing new came in, the list is left
   completely alone, and if you have a comment selected when a new one
   does arrive, that same comment stays selected afterward rather than
   losing your place. A background check that fails (e.g. a brief
   connection drop) is silent and just retries on the next tick, so it
   never interrupts you with an error for something that will very likely
   succeed 60 seconds later. Select a comment to see, below the list:
   - Its **checklist**, if it has one, as tickable checkboxes — ticking or
     unticking one syncs that change back to Kitsu immediately. If the
     sync fails (e.g. connection drop), the checkbox reverts and you'll
     get a warning, so the UI never shows a checked state that isn't
     actually saved on the server.
   - Its **revisions**, embedded inline, with any Kitsu **drawover**
     (annotations drawn during a review session — shapes, arrows,
     freehand markup, text notes) composited directly on top of the
     image, labeled "(with drawover)". If a revision has a drawover,
     click **Add Overpaint as Image Plane** to show it in the viewport as an image
     plane named `Feedback_<revision>` (e.g. `Feedback_2`), attached to
     the active viewport's camera — so you can reference the notes while
     working. Clicking it again for the same revision replaces that
     image plane rather than stacking duplicates; delete the
     `Feedback_<revision>` node when you're done with it.
   - Its **attachments** (other reference images), embedded inline — no
     separate download step needed for viewing. Click **Save
     Attachment(s)** if you want a local copy.

### Frame-based feedback on video revisions

When a revision is a **video** (e.g. a published playblast), reviewers in
Kitsu draw their notes on specific frames, and the panel shows the
revision as its thumbnail with the list of annotated frames plus two
buttons:

- **Add Timeline Markers** creates a native Time Slider Bookmark (Maya
  2020+) on every annotated frame — colored teal bands on the time
  slider, labeled `Kitsu r<revision> f<frame>`, also listed under
  **Windows → Animation Editors → Time Slider Bookmark**. Re-clicking
  replaces that revision's markers instead of stacking duplicates; they
  are plain nodes, so deleting them (or using the bookmark editor)
  removes them again.
- **Show Drawovers in Viewport** attaches an image plane named
  `Feedback_<revision>` to the active viewport's camera that shows each
  frame's drawover **only on that exact frame** — the markup is rendered
  onto transparent images (one per annotated frame) and an expression
  switches the plane to a fully transparent blank on all other frames.
  So stepping to a marked frame shows the reviewer's strokes over your
  live scene at that frame, and scrubbing away hides them. Re-clicking
  replaces the plane; delete the `Feedback_<revision>` node when done.

Frame mapping: Kitsu counts video frames from 0, and frame 0 of a
published playblast is the first frame of your playback range — so
markers land at `playback start + Kitsu frame`. If you publish
playblasts of a different range than you work in, set the playback range
to the published range before adding markers. The frame⇄time conversion
uses the Kitsu production's fps, falling back to the scene's fps if the
production doesn't define one.
4. To publish, pick a status, write a comment, and click **Publish** — the
   plugin captures the active viewport at the current frame (a
   single-frame playblast, so what you see is what gets published) and
   publishes it as a proper Kitsu **preview/revision** (not just a plain
   comment attachment), so it shows up in the task's version history and
   can be drawn over in review sessions. If the scene filename ends in a
   `_v###`-style version (e.g. `Hero_modeling_v002.ma`), that number is
   used as the revision number on Kitsu too, so your local version and
   the Kitsu revision always match up.

   **Variations**: before clicking Publish, queue extra images with
   **+ From Viewport** (captures the current view right now, e.g. after
   toggling a render layer or display layer) or **+ From File...**
   (browse for an existing image, e.g. exported from another app) —
   listed above the Publish button, with **Remove Selected** to drop one
   before publishing. These are uploaded alongside the main capture as
   additional preview files on the **same revision/comment** (Kitsu's own
   multi-preview-per-revision support) — not separate revisions and not
   plain comment attachments, so they show up as flippable variations on
   that one revision, e.g. a beauty pass next to a wireframe or clay
   render of the same version. The queue is cleared as each variation
   uploads successfully; if the main publish fails, none are touched, and
   if publishing succeeds but a particular variation's upload fails, only
   that one stays queued (with a clear warning) so it's easy to retry
   without re-adding it.

### Publish Capture: full-scene video export

The Publish button above exports a single frame. For a full-scene H.264
MP4 of your current playback range, published to Kitsu as a new
revision, use **Kitsu/Zou → Publish Capture** in the main menu.

1. **Requirements**: the panel must already be open, logged in, and have
   a task detected (Publish Capture reuses that state rather than asking
   you to log in again), and the scene must be saved. **`ffmpeg` must be
   on your system PATH** — Maya's own `playblast` command can't produce
   H.264/MP4 directly on modern installs (that used to rely on
   QuickTime, long discontinued on Windows and not bundled on macOS
   either), so this renders a PNG sequence via `playblast` and encodes it
   with `ffmpeg`. Most studios already have `ffmpeg` installed for
   exactly this; if it's missing, the error message says so explicitly
   rather than failing deep inside Maya's own codecs. Get it from
   [ffmpeg.org](https://ffmpeg.org/) if you don't have it.
2. A small dialog asks for a **task status** and a **comment**, the same
   two fields the panel's own Publish section asks for.
3. The full current playback range (Range Slider min/max) is captured at
   the scene's frame rate, encoded to H.264, and saved locally, then
   **published to Kitsu as a new revision** on the detected task with the
   status/comment from the dialog — the video shows up in the task's
   version history exactly like the single-frame Publish button's PNG
   does. If the upload itself fails (e.g. a dropped connection), the
   video stays on disk and the error message says exactly where, so nothing
   is lost — only the exported file is deleted or wasted.

**Output**: saved as `<scene folder>/capture/<name>.mp4`. The file name
follows:

```
<Show>[_<Episode>]_<Sequence>_<Shot>_v<version>_<Initial><Lastname>.mp4
```

e.g. `MyShow_seq010_sh0100_v003_JDoe.mp4`, or with an episode for a TV
show, `MyShow_ep01_seq010_sh0100_v003_JDoe.mp4`. For an asset scene
(which has no episode/sequence/shot), the analogous fallback substitutes
the asset type and asset name, e.g. `MyShow_characters_Hero_v003_JDoe.mp4`.
The version number comes from the scene's own `_v###` filename (omitted
if there isn't one, and also used as the Kitsu revision number, matching
the single-frame Publish button's convention); the trailing name is the
logged-in Kitsu user's first initial + last name (e.g. "John Doe" →
"JDoe"), falling back to splitting `full_name` if an account has no
separate first/last name set.

Naming conventions are one of the most studio-specific parts of a
pipeline, so this is kept in its own small function — swap
`capture_naming.py`'s `build_capture_filename` if yours differs, without
touching the render/encode/upload logic in `publish_capture.py`.

### Drawover rendering limitations

Drawover markup is Kitsu's own Fabric.js canvas data (rectangles,
circles, arrows, freehand strokes, text notes), re-drawn locally in
[annotations.py](modules/kitsu_maya_plugin/scripts/kitsu_maya_plugin/annotations.py)
with `QPainter` — this covers everything Kitsu's own drawover toolbar
produces. Not (yet) supported: the eraser tool's mask, nested groups,
and rotation on freehand strokes/lines/arrows specifically (rotation on
rectangles/circles/text works). A shape that can't be drawn is skipped
rather than breaking the rest of the drawover.

## Notes on security

The account password is never written to disk — only the server URL,
email, project ID, and API token are persisted (via `QSettings`) across
Maya restarts.

The API token field holds a **refresh token**, not your password: after
logging in with email/password, this plugin saves the refresh token
Kitsu's own auth system issued for that login, so future logins can use
it instead of typing the password again (see step 1 above) — you can
also paste one in yourself if you already have one. Unlike a password,
it's revocable and time-limited (expiring on its own after however long
the server allows, independent of anything this plugin controls); if it
ever expires or is revoked, logging in with email/password once more
simply gets and saves a fresh one. It's stored in plain text the same
way the other non-secret fields are, so treat it with the same care
you'd give any bearer credential — anyone with it can act as your
account until it expires.
