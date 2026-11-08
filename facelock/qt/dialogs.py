"""Onboarding wizard, face enrollment dialog and the app picker."""

import os

import psutil
from PySide6.QtCore import QFileInfo, QSize, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileIconProvider, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import config, winapi
from ..vision import Enroller
from . import theme
from .widgets import FaceScanner, IconBadge, PinField, ToggleSwitch, label

_icon_provider = None
_icon_cache = {}

SYSTEM_EXES = {"system", "registry", "svchost.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
               "lsass.exe", "smss.exe", "dwm.exe", "fontdrvhost.exe", "conhost.exe", "runtimebroker.exe",
               "explorer.exe", "memory compression", "facelock.exe", "python.exe", "pythonw.exe",
               "applicationframehost.exe", "textinputhost.exe", "shellexperiencehost.exe",
               "searchhost.exe", "startmenuexperiencehost.exe", "systemsettings.exe", "taskmgr.exe",
               "lockapp.exe", "ctfmon.exe", "sihost.exe", "taskhostw.exe", "widgets.exe"}


def app_icon(path):
    global _icon_provider
    if not path:
        return theme.glyph_icon("app", theme.ACCENT)
    if path not in _icon_cache:
        if _icon_provider is None:
            _icon_provider = QFileIconProvider()
        _icon_cache[path] = _icon_provider.icon(QFileInfo(path))
    return _icon_cache[path]


def running_apps(include_background=False):
    """[(exe_name, path, title)] sorted, windowed apps first."""
    titles = winapi.visible_window_titles()
    apps = {}
    for p in psutil.process_iter(["pid", "name"]):
        name = (p.info["name"] or "").lower()
        if not name.endswith(".exe") or name in SYSTEM_EXES:
            continue
        title = titles.get(p.pid, "")
        if not title and not include_background:
            continue
        if name not in apps or (title and not apps[name][2]):
            try:
                path = p.exe()
            except psutil.Error:
                path = ""
            apps[name] = (name, path, title)
    return sorted(apps.values(), key=lambda a: (not a[2], a[0]))


def pretty_app(exe):
    base = exe[:-4] if exe.lower().endswith(".exe") else exe
    return base[:1].upper() + base[1:]


class AppPicker(QDialog):
    """Pick one or more running apps. picked(list of (name, path))."""

    picked = Signal(list)

    def __init__(self, parent, already=()):
        super().__init__(parent)
        self.setWindowTitle("Add apps to lock")
        self.resize(480, 560)
        self.already = set(already)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.addWidget(label("Add apps to lock", "H2"))
        lay.addWidget(label("Tick the apps that should need Face ID to open.", "Muted"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(28, 28))
        lay.addWidget(self.list, 1)
        self.bg = QCheckBox("Show background processes")
        self.bg.toggled.connect(self._load)
        lay.addWidget(self.bg)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Lock selected")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self._load()

    def _load(self):
        self.list.clear()
        for name, path, title in running_apps(self.bg.isChecked()):
            if name in self.already:
                continue
            item = QListWidgetItem(app_icon(path), f"{pretty_app(name)}\n{title or name}")
            item.setData(Qt.UserRole, (name, path))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.list.addItem(item)
        self._filter(self.search.text())

    def _filter(self, text):
        text = text.lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(text not in item.text().lower())

    def _accept(self):
        chosen = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                  if self.list.item(i).checkState() == Qt.Checked]
        self.picked.emit(chosen)
        self.accept()


class EnrollDialog(QDialog):
    """Scan the face. completed() is emitted after templates are saved."""

    completed = Signal()

    def __init__(self, app, append, parent=None):
        super().__init__(parent)
        self.app, self.append = app, append
        self.setWindowTitle("Add an alternate look" if append else "Set up Face ID")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 22)
        page = ScanPage(app, append)
        page.done.connect(self._done)
        lay.addWidget(page)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        lay.addWidget(cancel, 0, Qt.AlignRight)
        self.page = page
        page.begin()

    def _done(self):
        self.completed.emit()
        self.accept()

    def reject(self):
        self.page.scanner.stop()
        super().reject()


class ScanPage(QWidget):
    done = Signal()

    def __init__(self, app, append=False):
        super().__init__()
        self.app, self.append = app, append
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addStretch()
        t = label("Add an alternate look" if append else "Scan your face", "H1")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        sub = label("Good, even lighting works best. Take off anything covering your face.", "Muted", wrap=True)
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(12)
        self.scanner = FaceScanner(240)
        lay.addWidget(self.scanner, 0, Qt.AlignHCenter)
        self.status = label("Starting camera…")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(self.status)
        self.scanner.statusChanged.connect(self.status.setText)
        self.scanner.finished.connect(self._finished)
        lay.addStretch()

    def begin(self):
        self.enroller = Enroller(self.app.engine)
        self.scanner.start(self.app.hub, self.enroller)

    def _finished(self):
        self.app.engine.set_templates(self.enroller.embeddings(), append=self.append)
        config.log_event("system", "Added an alternate look" if self.append else "Face ID set up")
        self.done.emit()


class Onboarding(QDialog):
    finished_setup = Signal()

    def __init__(self, app):
        super().__init__(None, Qt.WindowCloseButtonHint)
        self.app = app
        self.setWindowTitle("Welcome to FaceLock")
        self.setFixedSize(560, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 30, 36, 26)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.dots = QHBoxLayout()
        self.dots.addStretch()
        self._dot_labels = []
        for _ in range(5):
            d = QLabel()
            d.setFixedSize(8, 8)
            self._dot_labels.append(d)
            self.dots.addWidget(d)
        self.dots.addStretch()
        root.addLayout(self.dots)

        self.stack.addWidget(self._welcome())
        self.scan = ScanPage(app)
        self.scan.done.connect(lambda: self.go(2))
        self.stack.addWidget(self.scan)
        self.stack.addWidget(self._pin_page())
        self.stack.addWidget(self._apps_page())
        self.stack.addWidget(self._done_page())
        self.go(0)

    def go(self, i):
        self.stack.setCurrentIndex(i)
        for n, d in enumerate(self._dot_labels):
            col = theme.ACCENT if n == i else theme.current["border"]
            d.setStyleSheet(f"background: {col}; border-radius: 4px;")
        if i == 1:
            self.scan.begin()
        if i == 2:
            self.pin1.setFocus()
        if i == 3:
            self._fill_apps()

    def _page(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        return w, lay

    def _welcome(self):
        w, lay = self._page()
        lay.addStretch()
        logo = QLabel()
        logo.setPixmap(theme.logo_pixmap(96))
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)
        t = label("Face ID for your PC", "H1")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        s = label("Unlock with a glance using the webcam you already have.", "Muted")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        lay.addSpacing(18)
        for icon, title, text in (("app", "Lock apps", "Apps you choose need your face to open."),
                                  ("lock", "Lock your PC", "One shortcut, or automatically when you walk away."),
                                  ("shield", "Private by design", "Runs offline. Your face never leaves this PC.")):
            row = QHBoxLayout()
            row.setSpacing(14)
            row.addWidget(IconBadge(icon, size=40))
            col = QVBoxLayout()
            col.setSpacing(0)
            tl = label(title)
            tl.setStyleSheet("font-weight: 600;")
            col.addWidget(tl)
            col.addWidget(label(text, "Muted"))
            row.addLayout(col, 1)
            lay.addLayout(row)
            lay.addSpacing(6)
        lay.addStretch()
        b = QPushButton("Get started")
        b.setObjectName("Primary")
        b.setMinimumHeight(42)
        b.clicked.connect(lambda: self.go(1))
        lay.addWidget(b)
        return w

    def _pin_page(self):
        w, lay = self._page()
        lay.addStretch()
        lay.addWidget(IconBadge("key", size=64), 0, Qt.AlignHCenter)
        t = label("Create a backup PIN", "H1")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        s = label("Use it when the camera can't see you: dark rooms, a covered lens, or a new haircut.",
                  "Muted", wrap=True)
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        lay.addSpacing(14)
        self.pin1 = PinField("New PIN (4+ characters)")
        self.pin2 = PinField("Confirm PIN")
        self.pin2.returnPressed.connect(self._save_pin)
        for f in (self.pin1, self.pin2):
            f.setFixedWidth(300)
            lay.addWidget(f, 0, Qt.AlignHCenter)
        self.pin_err = label("")
        self.pin_err.setStyleSheet(f"color: {theme.RED};")
        self.pin_err.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.pin_err)
        lay.addStretch()
        b = QPushButton("Continue")
        b.setObjectName("Primary")
        b.setMinimumHeight(42)
        b.clicked.connect(self._save_pin)
        lay.addWidget(b)
        return w

    def _save_pin(self):
        a, b = self.pin1.text(), self.pin2.text()
        if len(a) < 4:
            self.pin_err.setText("Use at least 4 characters.")
        elif a != b:
            self.pin_err.setText("PINs don't match.")
        else:
            self.app.cfg.set_pin(a)
            self.go(3)

    def _apps_page(self):
        w, lay = self._page()
        t = label("Lock some apps?", "H1")
        lay.addWidget(t)
        lay.addWidget(label("These are open right now. You can add more any time.", "Muted"))
        self.app_list = QListWidget()
        self.app_list.setIconSize(QSize(28, 28))
        lay.addWidget(self.app_list, 1)
        row = QHBoxLayout()
        skip = QPushButton("Skip")
        skip.clicked.connect(lambda: self.go(4))
        row.addWidget(skip)
        row.addStretch()
        b = QPushButton("Lock selected")
        b.setObjectName("Primary")
        b.setMinimumHeight(42)
        b.clicked.connect(self._save_apps)
        row.addWidget(b)
        lay.addLayout(row)
        return w

    def _fill_apps(self):
        self.app_list.clear()
        for name, path, title in running_apps():
            item = QListWidgetItem(app_icon(path), f"{pretty_app(name)}\n{title}")
            item.setData(Qt.UserRole, (name, path))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.app_list.addItem(item)

    def _save_apps(self):
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            if item.checkState() == Qt.Checked:
                self.app.add_locked_app(*item.data(Qt.UserRole))
        self.go(4)

    def _done_page(self):
        w, lay = self._page()
        lay.addStretch()
        lay.addWidget(IconBadge("check", theme.GREEN, size=72), 0, Qt.AlignHCenter)
        t = label("You're protected", "H1")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        hk = self.app.cfg["hotkey"].upper().replace("+", " + ")
        s = label(f"Press  {hk}  any time to lock your PC.\nFaceLock lives in the system tray.", "Muted")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        lay.addSpacing(20)
        self.startup = ToggleSwitch(True)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(label("Start FaceLock with Windows"))
        row.addSpacing(10)
        row.addWidget(self.startup)
        row.addStretch()
        lay.addLayout(row)
        lay.addStretch()
        b = QPushButton("Finish")
        b.setObjectName("Primary")
        b.setMinimumHeight(42)
        b.clicked.connect(self._finish)
        lay.addWidget(b)
        return w

    def _finish(self):
        on = self.startup.isChecked()
        self.app.cfg["start_with_windows"] = on
        config.set_start_with_windows(on)
        self.app.cfg["onboarded"] = True
        self.finished_setup.emit()
        self.accept()

    def reject(self):
        self.scan.scanner.stop()
        super().reject()


def exe_name(path):
    return os.path.basename(path).lower()
