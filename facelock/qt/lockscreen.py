"""The Face ID prompt popup and the full-screen system lock."""

import getpass
import time

import psutil
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from .. import config, winapi
from ..vision import Verifier
from . import theme
from .widgets import FaceScanner, PinField, label


def fade(widget, start, end, ms=200, on_done=None):
    anim = QPropertyAnimation(widget, b"windowOpacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    if on_done:
        anim.finished.connect(on_done)
    anim.start()
    return anim


class AuthPopup(QWidget):
    """Compact Face ID prompt. Emits done(ok, method)."""

    done = Signal(bool, str)

    def __init__(self, app, title, subtitle, icon=None, timeout=45):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.app = app
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("FaceLock")
        self._finished = False
        self._drag = None
        self.timeout = timeout

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        box = QFrame()
        box.setObjectName("Card")
        box.setStyleSheet("#Card { border-radius: 20px; }")
        shadow = QGraphicsDropShadowEffect(blurRadius=40, xOffset=0, yOffset=8)
        shadow.setColor(QColor(0, 0, 0, 110))
        box.setGraphicsEffect(shadow)
        outer.addWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(28, 22, 28, 18)
        lay.setSpacing(6)

        top = QHBoxLayout()
        brand = QLabel()
        brand.setPixmap(theme.logo_pixmap(20))
        top.addWidget(brand)
        top.addWidget(label("FaceLock", "Faint"))
        top.addStretch()
        close = QPushButton(theme.ICON["close"])
        close.setObjectName("IconButton")
        close.setStyleSheet(theme.icon_css(11))
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(lambda: self.finish(False, "cancel"))
        top.addWidget(close)
        lay.addLayout(top)

        if icon is not None and not icon.isNull():
            ic = QLabel()
            ic.setPixmap(icon.pixmap(48, 48))
            ic.setAlignment(Qt.AlignCenter)
            lay.addWidget(ic)
        t = label(title, "H2")
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet("font-size: 19px;")
        lay.addWidget(t)
        s = label(subtitle, "Muted")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)
        lay.addSpacing(8)

        self.scanner = FaceScanner(190)
        lay.addWidget(self.scanner, 0, Qt.AlignHCenter)
        self.status = label("Starting camera…")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.status)
        self.scanner.statusChanged.connect(self.status.setText)
        self.scanner.finished.connect(lambda: self.finish(True, "face"))

        self.pin_box = QWidget()
        pl = QHBoxLayout(self.pin_box)
        pl.setContentsMargins(0, 6, 0, 0)
        self.pin = PinField()
        self.pin.returnPressed.connect(self._submit_pin)
        pl.addWidget(self.pin)
        go = QPushButton("Unlock")
        go.setObjectName("Primary")
        go.clicked.connect(self._submit_pin)
        pl.addWidget(go)
        self.pin_box.hide()
        lay.addWidget(self.pin_box)
        self.err = label("", None)
        self.err.setStyleSheet(f"color: {theme.RED};")
        self.err.setAlignment(Qt.AlignCenter)
        self.err.hide()
        lay.addWidget(self.err)

        row = QHBoxLayout()
        self.pin_link = QPushButton("Use PIN instead")
        self.pin_link.setObjectName("Link")
        self.pin_link.setCursor(Qt.PointingHandCursor)
        self.pin_link.clicked.connect(self.show_pin)
        self.pin_link.setVisible(app.cfg.has_pin)
        row.addWidget(self.pin_link)
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(lambda: self.finish(False, "cancel"))
        row.addWidget(cancel)
        lay.addLayout(row)
        self.countdown = QProgressBar()
        self.countdown.setTextVisible(False)
        self.countdown.setFixedHeight(3)
        self.countdown.setRange(0, timeout * 10)
        self.countdown.setValue(timeout * 10)
        lay.addWidget(self.countdown)

        self.started = time.monotonic()
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._tick)
        self.tick.start(100)
        self.scanner.start(app.hub, Verifier(app.engine, app.cfg, context=title.replace("Unlock ", "")))
        self.adjustSize()
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2 - 40)
        self.setWindowOpacity(0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._fade = fade(self, 0, 1, 180)

    def _tick(self):
        left = self.timeout - (time.monotonic() - self.started)
        self.countdown.setValue(max(0, int(left * 10)))
        if left <= 0:
            self.finish(False, "timeout")
        elif self.scanner.camera_failed and not self.pin_box.isVisible() and self.app.cfg.has_pin:
            self.show_pin()

    def show_pin(self):
        self.pin_box.show()
        self.pin_link.hide()
        self.pin.setFocus()
        self.adjustSize()

    def _submit_pin(self):
        ok, msg = self.app.try_pin(self.pin.text())
        self.pin.clear()
        if ok:
            self.finish(True, "pin")
        else:
            self.err.setText(msg)
            self.err.show()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.finish(False, "cancel")
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def finish(self, ok, method):
        if self._finished:
            return
        self._finished = True
        self.tick.stop()
        self.scanner.stop()
        self.done.emit(ok, method)
        self._fade = fade(self, self.windowOpacity(), 0, 150, self.close)


def blurred(pixmap, strength=28):
    if pixmap.isNull():
        return pixmap
    w, h = pixmap.width(), pixmap.height()
    small = pixmap.scaled(max(1, w // strength), max(1, h // strength), Qt.IgnoreAspectRatio,
                          Qt.SmoothTransformation)
    mid = small.scaled(max(1, w // 6), max(1, h // 6), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return mid.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


class LockWindow(QWidget):
    def __init__(self, screen, background):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.background = background
        self.allow_close = False
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.ArrowCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        if self.background and not self.background.isNull():
            p.drawPixmap(r, self.background)
            p.fillRect(r, QColor(8, 10, 16, 150))
        else:
            g = QLinearGradient(0, 0, r.width(), r.height())
            g.setColorAt(0, QColor("#141a2a"))
            g.setColorAt(1, QColor("#07080c"))
            p.fillRect(r, g)
        shade = QLinearGradient(0, 0, 0, r.height())
        shade.setColorAt(0, QColor(0, 0, 0, 40))
        shade.setColorAt(1, QColor(0, 0, 0, 120))
        p.fillRect(r, shade)
        p.end()

    def closeEvent(self, e):
        if self.allow_close:
            e.accept()
        else:
            e.ignore()


WHITE = "color: white;"
SOFT = "color: rgba(255,255,255,0.72);"


class LockScreen:
    """Covers every monitor until Face ID or PIN succeeds. on_unlock(method) is called after."""

    def __init__(self, app, on_unlock):
        self.app, self.on_unlock = app, on_unlock
        self.windows = []
        self.clocks = []
        self._unlocking = False
        self._last_proc_check = 0.0
        blur = app.cfg["lock_screen_blur"]
        primary_screen = QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens():
            bg = blurred(screen.grabWindow(0)) if blur else None
            win = LockWindow(screen, bg)
            self._build(win, screen is primary_screen)
            self.windows.append(win)
            if screen is primary_screen:
                self.primary = win
            win.setWindowOpacity(0)
            win.show()
            win.windowHandle().setScreen(screen)
            win.setGeometry(screen.geometry())
            win._fade = fade(win, 0, 1, 260)
        self.blocker = winapi.KeyboardBlocker()
        self.blocker.start_and_wait()
        self.guard = QTimer()
        self.guard.timeout.connect(self._guard)
        self.guard.start(250)
        self._guard()
        self.scanner.start(app.hub, Verifier(app.engine, app.cfg, context="your PC"))

    def _build(self, win, primary):
        lay = QVBoxLayout(win)
        lay.setContentsMargins(40, 0, 40, 36)
        lay.addStretch(2 if primary else 5)
        clock = QLabel()
        clock.setAlignment(Qt.AlignCenter)
        clock.setStyleSheet(WHITE + 'font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 104px;'
                                    "font-weight: 300; background: transparent;")
        date = QLabel()
        date.setAlignment(Qt.AlignCenter)
        date.setStyleSheet(SOFT + "font-size: 22px; background: transparent;")
        lay.addWidget(clock)
        lay.addWidget(date)
        self.clocks.append((clock, date))
        if not primary:
            lay.addStretch(6)
            return
        lay.addStretch(1)

        self.scanner = FaceScanner(200)
        lay.addWidget(self.scanner, 0, Qt.AlignHCenter)
        user = QLabel(getpass.getuser())
        user.setAlignment(Qt.AlignCenter)
        user.setStyleSheet(WHITE + "font-size: 24px; font-weight: 600; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(user)
        self.status = QLabel("Starting camera…")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(SOFT + "font-size: 15px; background: transparent;")
        lay.addWidget(self.status)
        self.scanner.statusChanged.connect(self.status.setText)
        self.scanner.finished.connect(lambda: self.unlock("face"))

        self.pin_box = QWidget()
        self.pin_box.setFixedWidth(320)
        pl = QVBoxLayout(self.pin_box)
        pl.setContentsMargins(0, 10, 0, 0)
        self.pin = PinField("PIN")
        self.pin.setStyleSheet("font-size: 17px; letter-spacing: 4px; color: white; padding: 10px;"
                               "background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.25);"
                               "border-radius: 10px;")
        self.pin.returnPressed.connect(self._submit_pin)
        pl.addWidget(self.pin)
        self.pin_err = QLabel("")
        self.pin_err.setAlignment(Qt.AlignCenter)
        self.pin_err.setStyleSheet(f"color: {theme.RED}; background: transparent;")
        pl.addWidget(self.pin_err)
        self.pin_box.hide()
        lay.addWidget(self.pin_box, 0, Qt.AlignHCenter)

        self.pin_link = QPushButton("Use PIN instead")
        self.pin_link.setCursor(Qt.PointingHandCursor)
        self.pin_link.setStyleSheet("QPushButton { color: white; background: rgba(255,255,255,0.12);"
                                    "border: 1px solid rgba(255,255,255,0.2); border-radius: 16px;"
                                    "padding: 7px 18px; } QPushButton:hover { background: rgba(255,255,255,0.2); }")
        self.pin_link.clicked.connect(self.show_pin)
        self.pin_link.setVisible(self.app.cfg.has_pin)
        lay.addSpacing(10)
        lay.addWidget(self.pin_link, 0, Qt.AlignHCenter)
        lay.addStretch(3)
        foot = QLabel(f"{theme.ICON['lock']}  ")
        foot.setFont(theme.icon_font(12))
        foot_text = QLabel("Locked by FaceLock")
        foot_text.setStyleSheet(SOFT + "font-size: 12px; background: transparent;")
        foot.setStyleSheet(SOFT + "background: transparent;" + theme.icon_css(12))
        frow = QHBoxLayout()
        frow.addStretch()
        frow.addWidget(foot)
        frow.addWidget(foot_text)
        frow.addStretch()
        lay.addLayout(frow)

    def show_pin(self):
        self.pin_link.hide()
        self.pin_box.show()
        self.pin.setFocus()

    def _submit_pin(self):
        ok, msg = self.app.try_pin(self.pin.text())
        self.pin.clear()
        if ok:
            self.unlock("pin")
        else:
            self.pin_err.setText(msg)
            config.log_event("pin", "Wrong PIN on lock screen", ok=False)

    def _guard(self):
        if self._unlocking:
            return
        now_t = time.localtime()
        for clock, date in self.clocks:
            clock.setText(time.strftime("%H:%M", now_t))
            date.setText(time.strftime("%A, %B ", now_t) + str(now_t.tm_mday))
        for w in self.windows:
            w.raise_()
        if not self.primary.isActiveWindow():
            self.primary.activateWindow()
        if self.scanner.camera_failed and self.app.cfg.has_pin and not self.pin_box.isVisible():
            self.show_pin()
        now = time.monotonic()
        if now - self._last_proc_check > 1:
            self._last_proc_check = now
            for p in psutil.process_iter(["name"]):
                if (p.info["name"] or "").lower() == "taskmgr.exe":
                    try:
                        p.kill()  # works only when FaceLock runs elevated
                    except psutil.Error:
                        pass

    def unlock(self, method):
        if self._unlocking:
            return
        self._unlocking = True
        self.guard.stop()
        self.blocker.stop()
        self.scanner.stop()
        delay = 0 if method == "face" else 1

        def close_all():
            for w in self.windows:
                w.allow_close = True
                w.close()
            self.windows = []
            self.on_unlock(method)
        for w in self.windows:
            w._fade = fade(w, 1, 0, 280)
        QTimer.singleShot(300 + delay, close_all)
