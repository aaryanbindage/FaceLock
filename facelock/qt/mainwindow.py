"""The FaceLock dashboard window."""

import os
import time
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import __version__, config, winapi
from ..vision import probe_cameras
from . import theme
from .dialogs import AppPicker, EnrollDialog, app_icon, pretty_app
from .widgets import Card, HotkeyEdit, IconBadge, PinField, Segmented, SettingRow, ToggleSwitch, label

PAGES = [("home", "Home", "home"), ("face", "Face ID", "face"), ("apps", "App Lock", "app"),
         ("system", "System Lock", "lock"), ("security", "Security", "shield"),
         ("activity", "Activity", "history")]

STRICTNESS = [("0.40", "Relaxed"), ("0.45", "Balanced"), ("0.52", "Strict")]
AWAY = [("15", "15 s"), ("30", "30 s"), ("60", "1 min"), ("120", "2 min"), ("300", "5 min")]
RELOCK = [(0, "Immediately after closing"), (1, "1 minute after closing"), (5, "5 minutes after closing"),
          (15, "15 minutes after closing"), (60, "1 hour after closing")]
EVENT_ICONS = {"unlock": "unlock", "app": "app", "lock": "lock", "intruder": "warning", "pin": "key",
               "system": "info"}


def button(text, primary=False, danger=False, slot=None):
    b = QPushButton(text)
    if primary:
        b.setObjectName("Primary")
    if danger:
        b.setObjectName("Danger")
    b.setCursor(Qt.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


def ago(ts):
    d = time.time() - ts
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    return datetime.fromtimestamp(ts).strftime("%b %d, %H:%M")


def nearest(options, value):
    return min(options, key=lambda o: abs(float(o[0]) - float(value)))[0]


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app, self.cfg = app, app.cfg
        self.setWindowTitle("FaceLock")
        self.setWindowIcon(theme.QIcon(theme.logo_pixmap(64)))
        self.resize(1000, 700)
        self.setMinimumSize(860, 560)
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(230)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 20, 14, 16)
        sl.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(theme.logo_pixmap(34))
        brand.addWidget(logo)
        name = label("FaceLock")
        name.setStyleSheet("font-size: 19px; font-weight: 700;")
        brand.addWidget(name)
        brand.addStretch()
        sl.addLayout(brand)
        sl.addSpacing(18)
        self.nav = QButtonGroup(self)
        self.nav_buttons = {}
        for key, text, icon in PAGES:
            b = QPushButton(f"  {text}")
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setIcon(theme.glyph_icon(icon, theme.current["muted"]))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.nav.addButton(b)
            self.nav_buttons[key] = b
            sl.addWidget(b)
        sl.addStretch()
        lock = button("  Lock now", primary=True, slot=app.lock_system)
        lock.setIcon(theme.glyph_icon("lock", "white"))
        lock.setMinimumHeight(40)
        sl.addWidget(lock)
        sl.addSpacing(6)
        ver = label(f"Version {__version__}", "Faint")
        ver.setAlignment(Qt.AlignCenter)
        sl.addWidget(ver)
        root.addWidget(side)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("Page")
        root.addWidget(self.scroll, 1)
        self.current = None
        self.show_page("home")
        self.refresher = QTimer(self)
        self.refresher.timeout.connect(self._auto_refresh)
        self.refresher.start(5000)

    # --- navigation ------------------------------------------------------------
    def show_page(self, key):
        self.current = key
        self.nav_buttons[key].setChecked(True)
        for k, b in self.nav_buttons.items():
            b.setIcon(theme.glyph_icon(dict((p[0], p[2]) for p in PAGES)[k],
                                       theme.current["text"] if k == key else theme.current["muted"]))
        page = QWidget()
        page.setObjectName("Page")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(16)
        getattr(self, f"_page_{key}")(lay)
        lay.addStretch()
        pos = self.scroll.verticalScrollBar().value()
        old = self.scroll.takeWidget()
        self.scroll.setWidget(page)
        if old:
            old.deleteLater()
        self.scroll.verticalScrollBar().setValue(pos if key == getattr(self, "_last_key", None) else 0)
        self._last_key = key

    def refresh(self):
        if self.current:
            self.show_page(self.current)

    def _auto_refresh(self):
        if self.isVisible() and self.current in ("home", "activity") and not self.isActiveWindow():
            self.refresh()

    def _header(self, lay, title, subtitle):
        lay.addWidget(label(title, "H1"))
        if subtitle:
            lay.addWidget(label(subtitle, "Muted", wrap=True))
        lay.addSpacing(4)

    def _toggle(self, key, on_change=None):
        t = ToggleSwitch(bool(self.cfg[key]))

        def changed(v):
            self.cfg[key] = v
            if on_change:
                on_change(v)
        t.toggled.connect(changed)
        return t

    # --- Home ----------------------------------------------------------------------
    def _page_home(self, lay):
        app = self.app
        hero = QFrame()
        hero.setObjectName("Card")
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(26, 24, 26, 24)
        hl.setSpacing(20)
        if not app.engine.enrolled:
            icon, color, title, sub = "warning", theme.ORANGE, "Face ID isn't set up", "Scan your face to start."
        elif app.paused:
            until = datetime.fromtimestamp(app.paused_until_wall).strftime("%H:%M")
            icon, color, title, sub = "pause", theme.ORANGE, "Protection paused", f"Resumes automatically at {until}."
        else:
            n = len(self.cfg["locked_apps"])
            icon, color, title = "shield", theme.GREEN, "You're protected"
            sub = (f"{n} app{'s' if n != 1 else ''} locked · Press {self.cfg['hotkey'].upper()} to lock your PC")
        hl.addWidget(IconBadge(icon, color, size=64))
        col = QVBoxLayout()
        col.addWidget(label(title, "Big"))
        col.addWidget(label(sub, "Muted", wrap=True))
        hl.addLayout(col, 1)
        if not app.engine.enrolled:
            hl.addWidget(button("Set up Face ID", primary=True, slot=lambda: self.enroll(False)))
        elif app.paused:
            hl.addWidget(button("Resume now", primary=True, slot=lambda: (app.resume(), self.refresh())))
        else:
            hl.addWidget(button("Pause 15 min", slot=lambda: app.pause(15 * 60, self.refresh)))
        lay.addWidget(hero)

        events = config.read_events(1000)
        day = time.time() - 86400
        week = time.time() - 7 * 86400
        unlocks = sum(1 for e in events if e["t"] > day and e["ok"] and e["kind"] in ("unlock", "app"))
        intruders = sum(1 for e in events if e["t"] > week and e["kind"] == "intruder")
        grid = QGridLayout()
        grid.setSpacing(16)
        for i, (icon, color, value, text, page) in enumerate((
                ("app", theme.ACCENT, len(self.cfg["locked_apps"]), "Locked apps", "apps"),
                ("unlock", theme.GREEN, unlocks, "Unlocks today", "activity"),
                ("warning", theme.ORANGE if intruders else theme.ACCENT, intruders, "Intruder attempts (7 days)",
                 "activity"))):
            card = QFrame()
            card.setObjectName("Card")
            card.setCursor(Qt.PointingHandCursor)
            card.mousePressEvent = lambda e, p=page: self.show_page(p)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(20, 18, 20, 18)
            cl.addWidget(IconBadge(icon, color))
            cl.addSpacing(6)
            cl.addWidget(label(str(value), "Big"))
            cl.addWidget(label(text, "Muted"))
            grid.addWidget(card, 0, i)
        lay.addLayout(grid)

        quick = Card("Quick settings")
        quick.add(SettingRow("app", "App Lock", "Ask for Face ID when locked apps open",
                             self._toggle("app_lock_enabled")))
        quick.add(SettingRow("walk", "Walk-away lock", "Lock when you leave or someone else sits down",
                             self._toggle("auto_lock_away")))
        quick.add(SettingRow("keyboard", "Lock shortcut", "Lock your PC from anywhere",
                             label(self.cfg["hotkey"].upper().replace("+", " + "), "Muted")))
        lay.addWidget(quick)

        recent = Card("Recent activity")
        shown = events[:5]
        if not shown:
            recent.lay.addWidget(label("Nothing yet. Unlocks and lock events will show up here.", "Muted"))
        for e in shown:
            recent.add(self._event_row(e, thumb=False))
        if events:
            recent.lay.addWidget(button("See all activity", slot=lambda: self.show_page("activity")), 0,
                                 Qt.AlignLeft)
        lay.addWidget(recent)

    # --- Face ID ---------------------------------------------------------------------
    def _page_face(self, lay):
        self._header(lay, "Face ID", "Your face is stored as encrypted numbers, never as photos.")
        n = len(self.app.engine.templates)
        c = Card()
        if n:
            c.add(SettingRow("face", "Face ID is set up", f"{n} face samples saved",
                             button("Scan again", slot=lambda: self.enroll(False)), color=theme.GREEN))
        else:
            c.add(SettingRow("face", "Face ID isn't set up", "Scan your face to start",
                             button("Set up", primary=True, slot=lambda: self.enroll(False)), color=theme.ORANGE))
        c.add(SettingRow("people", "Add an alternate look", "Glasses, hats, beard or very different lighting",
                         button("Add look", slot=lambda: self.enroll(True))))
        c.add(SettingRow("eye", "Test Face ID", "See how fast and reliably you're recognized",
                         button("Test", slot=self.app.test_face)))
        lay.addWidget(c)

        r = Card("Recognition")
        seg = Segmented(STRICTNESS, nearest(STRICTNESS, self.cfg["match_threshold"]))
        seg.changed.connect(lambda v: self.cfg.__setitem__("match_threshold", float(v)))
        r.add(SettingRow("shield", "Match strictness", "Stricter is safer but may need better lighting", seg))
        r.add(SettingRow("sync", "Liveness check",
                         "Turn your head slightly to unlock, so a photo of you won't work",
                         self._toggle("require_liveness")))
        r.add(SettingRow("sun", "Low-light boost", "Brighten dark camera images before recognizing",
                         self._toggle("low_light_boost")))
        cam = QComboBox()
        cam.addItems([f"Camera {i}" for i in range(4)])
        cam.setCurrentIndex(min(3, self.cfg["camera_index"]))
        cam.currentIndexChanged.connect(self.app.set_camera)
        cam_row = QWidget()
        crl = QHBoxLayout(cam_row)
        crl.setContentsMargins(0, 0, 0, 0)
        crl.addWidget(cam)
        crl.addWidget(button("Detect", slot=self._detect_cams))
        r.add(SettingRow("camera", "Camera", "Choose which webcam to use", cam_row))
        lay.addWidget(r)

        d = Card()
        d.add(SettingRow("delete", "Delete face data", "Remove all saved face samples from this PC",
                         button("Delete", danger=True, slot=self._delete_face), color=theme.RED))
        lay.addWidget(d)

    def enroll(self, append):
        dlg = EnrollDialog(self.app, append, self)
        dlg.completed.connect(self.refresh)
        dlg.exec()

    def _detect_cams(self):
        found = probe_cameras()
        QMessageBox.information(self, "FaceLock", "Working cameras: " + (
            ", ".join(f"Camera {i}" for i in found) if found else "none found"))

    def _delete_face(self):
        if QMessageBox.question(self, "FaceLock", "Delete all face data? You'll need your PIN until you "
                                                  "scan again.") == QMessageBox.Yes:
            self.app.engine.delete_templates()
            config.log_event("system", "Face data deleted")
            self.refresh()

    # --- App Lock --------------------------------------------------------------------
    def _page_apps(self, lay):
        self._header(lay, "App Lock", "Locked apps freeze the moment they start and continue only after Face ID. "
                                      "Cancel and they close.")
        c = Card()
        c.add(SettingRow("app", "App Lock", "Turn app protection on or off", self._toggle("app_lock_enabled")))
        relock = QComboBox()
        for minutes, text in RELOCK:
            relock.addItem(text, minutes)
        relock.setCurrentIndex(next((i for i, r in enumerate(RELOCK) if r[0] == self.cfg["relock_grace_minutes"]), 0))
        relock.currentIndexChanged.connect(lambda i: self.cfg.__setitem__("relock_grace_minutes", RELOCK[i][0]))
        c.add(SettingRow("sync", "Re-lock apps", "When an unlocked app needs Face ID again", relock))
        lay.addWidget(c)

        apps = Card()
        head = QHBoxLayout()
        head.addWidget(label("Locked apps", "H2"))
        head.addStretch()
        head.addWidget(button("Browse…", slot=self._browse_app))
        head.addWidget(button("+  Add apps", primary=True, slot=self._pick_apps))
        apps.lay.addLayout(head)
        apps.lay.addSpacing(6)
        locked = self.cfg["locked_apps"]
        if not locked:
            empty = label("No apps locked yet. Add messaging, photo or banking apps to keep them private.", "Muted",
                          wrap=True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(70)
            apps.lay.addWidget(empty)
        for exe in locked:
            path = self.cfg["app_paths"].get(exe, "")
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(4, 8, 4, 8)
            rl.setSpacing(14)
            ic = QLabel()
            ic.setPixmap(app_icon(path).pixmap(32, 32))
            rl.addWidget(ic)
            col = QVBoxLayout()
            col.setSpacing(0)
            t = label(pretty_app(exe))
            t.setStyleSheet("font-weight: 600;")
            col.addWidget(t)
            col.addWidget(label(path or exe, "Faint"))
            rl.addLayout(col, 1)
            rm = QPushButton(theme.ICON["delete"])
            rm.setObjectName("IconButton")
            rm.setStyleSheet(theme.icon_css(15))
            rm.setToolTip("Unlock this app permanently")
            rm.setCursor(Qt.PointingHandCursor)
            rm.clicked.connect(lambda _=False, e=exe: (self.app.remove_locked_app(e), self.refresh()))
            rl.addWidget(rm)
            apps.lay.addWidget(row)
        lay.addWidget(apps)

        if not self.app.is_admin:
            a = Card()
            a.add(SettingRow("admin", "Lock apps that run as administrator",
                             "Restart FaceLock as administrator to also lock elevated apps and close Task "
                             "Manager while your PC is locked.",
                             button("Restart as admin", slot=self.app.restart_as_admin)))
            lay.addWidget(a)

    def _pick_apps(self):
        dlg = AppPicker(self, self.cfg["locked_apps"])
        dlg.picked.connect(lambda items: ([self.app.add_locked_app(n, p) for n, p in items], self.refresh()))
        dlg.exec()

    def _browse_app(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an app", os.environ.get("ProgramFiles", "C:\\"),
                                              "Programs (*.exe)")
        if path:
            name = os.path.basename(path).lower()
            if name in ("facelock.exe", "explorer.exe"):
                QMessageBox.warning(self, "FaceLock", f"{name} can't be locked.")
                return
            self.app.add_locked_app(name, os.path.normpath(path))
            self.refresh()

    # --- System Lock ---------------------------------------------------------------------
    def _page_system(self, lay):
        self._header(lay, "System Lock", "Cover every screen until you look at the camera.")
        c = Card()
        hk = HotkeyEdit(self.cfg["hotkey"])
        hk.recording.connect(self.app.pause_hotkey)
        hk.chosen.connect(self._set_hotkey)
        c.add(SettingRow("keyboard", "Lock shortcut", "Click the box, then press a new shortcut", hk))
        c.add(SettingRow("lock", "Lock now", "Try the lock screen", button("Lock", slot=self.app.lock_system)))
        lay.addWidget(c)

        w = Card("Walk-away lock", "Uses the camera to notice when you leave, or when someone else is in front "
                                   "of the PC. The camera light stays on while this is enabled.")
        w.add(SettingRow("walk", "Lock when I walk away", None, self._toggle("auto_lock_away")))
        secs = Segmented(AWAY, nearest(AWAY, self.cfg["away_seconds"]))
        secs.changed.connect(lambda v: self.cfg.__setitem__("away_seconds", int(v)))
        w.add(SettingRow("history", "Lock after", "Time without seeing your face", secs))
        act = Segmented([("overlay", "FaceLock screen"), ("windows", "Windows lock")], self.cfg["away_action"])
        act.changed.connect(lambda v: self.cfg.__setitem__("away_action", v))
        w.add(SettingRow("monitor", "Lock with", "Windows lock is the most secure, but unlocks with your "
                                                 "Windows PIN or password", act))
        lay.addWidget(w)

        s = Card("Lock screen and sign-in")
        s.add(SettingRow("photo", "Blurred background", "Show a blurred view of your desktop behind the lock",
                         self._toggle("lock_screen_blur")))
        s.add(SettingRow("power", "Start with Windows", "Launch FaceLock when you sign in",
                         self._toggle("start_with_windows", config.set_start_with_windows)))
        s.add(SettingRow("face", "Face ID after sign-in", "Lock with FaceLock right after Windows starts",
                         self._toggle("lock_on_start")))
        s.add(SettingRow("shield", "Face ID after every Windows unlock",
                         "Two-factor: Windows PIN or password, then your face",
                         self._toggle("lock_after_windows_unlock")))
        lay.addWidget(s)

    def _set_hotkey(self, combo):
        err = self.app.set_hotkey(combo)
        if err:
            QMessageBox.warning(self, "FaceLock", err)
        self.refresh()

    # --- Security --------------------------------------------------------------------------
    def _page_security(self, lay):
        self._header(lay, "Security & privacy", None)
        c = Card()
        c.add(SettingRow("key", "Backup PIN", "Works everywhere Face ID is asked. 5 wrong tries pauses PIN entry.",
                         button("Change PIN", slot=self._change_pin)))
        c.add(SettingRow("shield", "Self-protection", "Restart FaceLock automatically if someone closes it",
                         self._toggle("self_protection", self.app.update_watchdog)))
        admin_text = "FaceLock is running as administrator" if self.app.is_admin else \
            "Needed to lock admin apps and close Task Manager while locked"
        c.add(SettingRow("admin", "Administrator mode", admin_text,
                         label("On", "Muted") if self.app.is_admin else
                         button("Restart as admin", slot=self.app.restart_as_admin)))
        lay.addWidget(c)

        i = Card("Intruder photos")
        i.add(SettingRow("camera", "Take a photo of unknown faces",
                         "Saved on this PC when someone else tries to unlock", self._toggle("save_intruders")))
        i.add(SettingRow("folder", "Intruder photos", None,
                         button("Open folder", slot=lambda: self._open(config.INTRUDER_DIR))))
        lay.addWidget(i)

        a = Card("Appearance")
        seg = Segmented([("system", "System"), ("light", "Light"), ("dark", "Dark")], self.cfg["theme"])
        seg.changed.connect(self._set_theme)
        a.add(SettingRow("sun", "Theme", None, seg))
        lay.addWidget(a)

        d = Card("Your data", "Face samples are encrypted with Windows DPAPI, so only your Windows account can "
                              "read them. Nothing is uploaded anywhere.")
        d.add(SettingRow("folder", "Data folder", str(config.APP_DIR),
                         button("Open", slot=lambda: self._open(config.APP_DIR))))
        d.add(SettingRow("delete", "Reset FaceLock", "Delete face data, PIN, locked apps and history",
                         button("Reset", danger=True, slot=self._reset), color=theme.RED))
        lay.addWidget(d)
        lay.addWidget(label("No app can block Ctrl+Alt+Del. For the strongest protection, run FaceLock as "
                            "administrator, keep self-protection on, or use the Windows lock for walk-away locking.",
                            "Faint", wrap=True))

    def _set_theme(self, value):
        self.cfg["theme"] = value
        self.app.apply_theme()
        self.refresh()

    @staticmethod
    def _open(path):
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _change_pin(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Change PIN")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.addWidget(label("Choose a new PIN", "H2"))
        p1, p2 = PinField("New PIN (4+ characters)"), PinField("Confirm PIN")
        err = label("")
        err.setStyleSheet(f"color: {theme.RED};")
        lay.addWidget(p1)
        lay.addWidget(p2)
        lay.addWidget(err)

        def save():
            if len(p1.text()) < 4:
                err.setText("Use at least 4 characters.")
            elif p1.text() != p2.text():
                err.setText("PINs don't match.")
            else:
                self.cfg.set_pin(p1.text())
                config.log_event("system", "PIN changed")
                dlg.accept()
        p2.returnPressed.connect(save)
        lay.addWidget(button("Save PIN", primary=True, slot=save))
        dlg.exec()

    def _reset(self):
        if QMessageBox.question(self, "Reset FaceLock", "Delete your face data, PIN, locked apps and history? "
                                                         "FaceLock will restart setup.") != QMessageBox.Yes:
            return
        self.app.reset_everything()

    # --- Activity ------------------------------------------------------------------------------
    def _page_activity(self, lay):
        head = QHBoxLayout()
        head.addWidget(label("Activity", "H1"))
        head.addStretch()
        head.addWidget(button("Clear history", slot=lambda: (config.clear_events(), self.refresh())))
        lay.addLayout(head)
        events = config.read_events(200)
        c = Card()
        if not events:
            c.lay.addWidget(label("No activity yet.", "Muted"))
        for e in events:
            c.add(self._event_row(e, thumb=True))
        lay.addWidget(c)

    def _event_row(self, e, thumb):
        kind = e.get("kind", "system")
        color = theme.GREEN if e.get("ok") else (theme.RED if kind in ("intruder", "pin") else theme.ORANGE)
        if kind in ("lock", "system") and e.get("ok"):
            color = theme.ACCENT
        ctrl = None
        if thumb and e.get("photo"):
            pm = QPixmap(str(config.INTRUDER_DIR / e["photo"]))
            if not pm.isNull():
                ctrl = QLabel()
                ctrl.setPixmap(pm.scaled(96, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                ctrl.setCursor(Qt.PointingHandCursor)
                ctrl.mousePressEvent = lambda _e, p=e["photo"]: os.startfile(config.INTRUDER_DIR / p)
        return SettingRow(EVENT_ICONS.get(kind, "info"), e.get("text", ""), ago(e["t"]), ctrl, color=color)
