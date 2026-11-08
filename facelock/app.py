"""FaceLock tray application: wires the camera, app lock, system lock and UI together."""

import argparse
import ctypes
import logging
import queue
import subprocess
import sys
import time
from collections import deque
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from . import config, watchdog, winapi
from .applock import AppLocker
from .presence import PresenceMonitor
from .qt import theme
from .qt.dialogs import Onboarding, app_icon, pretty_app
from .qt.lockscreen import AuthPopup, LockScreen
from .qt.mainwindow import MainWindow
from .vision import CameraHub, FaceEngine

log = logging.getLogger("facelock")


class SessionWatcher(QWidget):
    """Hidden window that receives Windows lock/unlock notifications."""

    def __init__(self, on_unlock):
        super().__init__()
        self.on_unlock = on_unlock
        winapi.register_session_notifications(int(self.winId()))

    def nativeEvent(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == winapi.WM_WTSSESSION_CHANGE and msg.wParam == winapi.WTS_SESSION_UNLOCK:
                self.on_unlock()
        return False, 0


class FaceLockApp:
    def __init__(self, qapp, args):
        self.qapp = qapp
        self.args = args
        self.cfg = config.Config()
        self.engine = FaceEngine(self.cfg)
        self.hub = CameraHub(self.cfg["camera_index"])
        self.is_admin = winapi.is_admin()
        self.apply_theme()

        self._ui = queue.Queue()
        self.locked = False
        self.lockscreen = None
        self.window = None
        self.onboarding = None
        self.auth_queue = deque()
        self.current_auth = None
        self.pin_fails = 0
        self.pin_lockouts = 0
        self.pin_block_until = 0.0
        self.paused_until = 0.0
        self.paused_until_wall = 0.0
        self.hotkey = None

        self.applocker = AppLocker(self.cfg, self._request_app_auth, log=lambda m: self.call(self.notify, m),
                                   is_paused=lambda: self.paused)
        self.presence = PresenceMonitor(self.cfg, self.engine, self.hub, lambda: self.locked or self.paused,
                                        lambda: self.call(self._on_away))
        self._build_tray()
        self.session = SessionWatcher(self._on_windows_unlock)
        self.pump = QTimer()
        self.pump.timeout.connect(self._pump)
        self.pump.start(30)
        self.guard = QTimer()
        self.guard.timeout.connect(self._housekeeping)
        self.guard.start(3000)

    # --- threading glue ------------------------------------------------------
    def call(self, fn, *args):
        """Run fn on the Qt thread (safe from any thread)."""
        self._ui.put((fn, args))

    def _pump(self):
        while True:
            try:
                fn, args = self._ui.get_nowait()
            except queue.Empty:
                return
            try:
                fn(*args)
            except Exception:
                log.exception("UI task failed")

    def notify(self, message, title="FaceLock"):
        log.info(message)
        self.tray.showMessage(title, message, QIcon(theme.logo_pixmap(64)), 4000)

    # --- appearance & tray -----------------------------------------------------
    def apply_theme(self):
        c = theme.apply(self.cfg["theme"])
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "setColorScheme"):
            hints.setColorScheme(Qt.ColorScheme.Dark if theme.is_dark else Qt.ColorScheme.Light)
        self.qapp.setStyleSheet(theme.stylesheet(c))

    def _build_tray(self):
        self.tray = QSystemTrayIcon(QIcon(theme.logo_pixmap(64)))
        self.tray.setToolTip("FaceLock")
        menu = QMenu()
        menu.addAction(theme.glyph_icon("home", theme.current["text"]), "Open FaceLock", self.open_settings)
        menu.addAction(theme.glyph_icon("lock", theme.current["text"]), "Lock PC now", self.lock_system)
        menu.addSeparator()
        self.pause_menu = menu.addMenu(theme.glyph_icon("pause", theme.current["text"]), "Pause protection")
        for text, secs in (("For 15 minutes", 900), ("For 1 hour", 3600), ("For 4 hours", 14400)):
            self.pause_menu.addAction(text, lambda s=secs: self.pause(s))
        self.resume_action = menu.addAction(theme.glyph_icon("play", theme.current["text"]), "Resume protection",
                                            self.resume)
        self.resume_action.setVisible(False)
        menu.addSeparator()
        menu.addAction(theme.glyph_icon("power", theme.current["text"]), "Quit FaceLock", self.request_quit)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.open_settings() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    # --- lifecycle -------------------------------------------------------------
    def start(self):
        config.QUIT_FLAG.unlink(missing_ok=True)
        err = self.set_hotkey(self.cfg["hotkey"])
        if err:
            self.notify(f"Lock shortcut unavailable: {err}")
        self.applocker.start()
        self.presence.start()
        winapi.listen_for_lock_requests(lambda: self.call(self.lock_system))
        self.update_watchdog(self.cfg["self_protection"])

        if not self.engine.enrolled or not self.cfg["onboarded"]:
            QTimer.singleShot(200, self.show_onboarding)
        elif self.args.lock or config.LOCK_MARKER.exists() or (self.args.startup and self.cfg["lock_on_start"]):
            QTimer.singleShot(200, self.lock_system)
        elif not self.args.startup:
            QTimer.singleShot(200, self.open_settings)
        else:
            QTimer.singleShot(1500, lambda: self.notify(
                f"FaceLock is protecting your PC. Press {self.cfg['hotkey'].upper()} to lock."))

    def show_onboarding(self):
        if self.onboarding:
            self.onboarding.raise_()
            return
        self.onboarding = Onboarding(self)

        def done():
            self.onboarding = None
            self._show_window()
            self.notify(f"Face ID is ready. Press {self.cfg['hotkey'].upper()} to lock your PC.")
        self.onboarding.finished_setup.connect(done)
        self.onboarding.rejected.connect(lambda: setattr(self, "onboarding", None))
        self.onboarding.show()
        self.onboarding.raise_()
        self.onboarding.activateWindow()

    def request_quit(self):
        self.authenticate("Quit FaceLock", "Confirm it's you to turn off protection",
                          lambda ok: ok and self.shutdown(), icon=None)

    def shutdown(self, restart_args=None):
        config.QUIT_FLAG.touch()
        config.log_event("system", "FaceLock was turned off")
        self.applocker.stop()
        self.presence.stop()
        if self.hotkey:
            self.hotkey.stop()
        self.tray.hide()
        if restart_args is not None:
            subprocess.Popen(config.launch_args("--replace", *restart_args), creationflags=0x08000000)
        QTimer.singleShot(150, self.qapp.quit)

    def _housekeeping(self):
        if self.cfg["self_protection"] and not watchdog.watchdog_alive():
            self.update_watchdog(True)
        if self.paused_until and not self.paused:
            self.resume()

    def update_watchdog(self, on):
        if on:
            config.QUIT_FLAG.unlink(missing_ok=True)
            if not watchdog.watchdog_alive():
                try:
                    watchdog.spawn(self.qapp.applicationPid())
                except OSError:
                    log.exception("could not start watchdog")
        else:
            config.QUIT_FLAG.touch()       # tells the watchdog to stand down
            QTimer.singleShot(2500, lambda: config.QUIT_FLAG.unlink(missing_ok=True))

    # --- authentication ------------------------------------------------------
    @property
    def paused(self):
        return time.monotonic() < self.paused_until

    def authenticate(self, title, subtitle, callback, icon=None, app_request=False):
        """Queue a Face ID prompt; callback(ok) runs on the UI thread."""
        self.auth_queue.append((title, subtitle, callback, icon, app_request))
        self._next_auth()

    def _next_auth(self):
        if self.current_auth or self.locked or not self.auth_queue:
            return
        title, subtitle, callback, icon, _ = self.auth_queue.popleft()
        if not self.engine.enrolled and not self.cfg.has_pin:
            callback(True)  # nothing set up yet, so nothing to protect with
            return
        popup = AuthPopup(self, title, subtitle, icon)

        def done(ok, method):
            self.current_auth = None
            callback(ok)
            if ok:
                # One look covers every app that was waiting at the same moment.
                waiting = [q for q in self.auth_queue if q[4]]
                for q in waiting:
                    self.auth_queue.remove(q)
                    q[2](True)
            QTimer.singleShot(200, self._next_auth)
        popup.done.connect(done)
        self.current_auth = popup

    def _request_app_auth(self, exe, path, callback):
        self.call(self._app_auth, exe, path, callback)

    def _app_auth(self, exe, path, callback):
        if path and not self.cfg["app_paths"].get(exe):
            self.cfg["app_paths"] = {**self.cfg["app_paths"], exe: path}
        name = pretty_app(exe)

        def result(ok):
            config.log_event("app", f"{'Opened' if ok else 'Blocked'} {name}", ok=ok, app=exe)
            callback(ok)
        self.authenticate(f"Unlock {name}", "Look at the camera to open this app", result,
                          icon=app_icon(path), app_request=True)

    def try_pin(self, pin):
        now = time.monotonic()
        if now < self.pin_block_until:
            return False, f"Too many attempts. Try again in {int(self.pin_block_until - now) + 1}s"
        if self.cfg.check_pin(pin):
            self.pin_fails = 0
            self.pin_lockouts = 0
            return True, ""
        self.pin_fails += 1
        if self.pin_fails >= 5:
            self.pin_fails = 0
            self.pin_lockouts += 1
            wait = min(60 * 2 ** (self.pin_lockouts - 1), 900)
            self.pin_block_until = now + wait
            config.log_event("pin", "Too many wrong PINs, PIN entry paused", ok=False)
            return False, f"Too many attempts. PIN paused for {wait // 60} min"
        return False, f"Wrong PIN · {5 - self.pin_fails} tries left"

    # --- pause -------------------------------------------------------------------
    def pause(self, seconds, after=None):
        def ok(v):
            if not v:
                return
            self.paused_until = time.monotonic() + seconds
            self.paused_until_wall = time.time() + seconds
            self.pause_menu.menuAction().setVisible(False)
            self.resume_action.setVisible(True)
            config.log_event("system", f"Protection paused for {seconds // 60} minutes")
            self.notify(f"Protection paused for {seconds // 60} minutes.")
            if after:
                after()
        self.authenticate("Pause protection", "Confirm it's you to pause FaceLock", ok)

    def resume(self):
        self.paused_until = 0.0
        self.pause_menu.menuAction().setVisible(True)
        self.resume_action.setVisible(False)
        self.notify("Protection resumed.")
        if self.window:
            self.window.refresh()

    # --- system lock -----------------------------------------------------------
    def lock_system(self):
        if self.locked:
            return
        if not self.engine.enrolled and not self.cfg.has_pin:
            self.notify("Set up Face ID first.")
            return
        self.locked = True
        config.LOCK_MARKER.touch()
        if self.current_auth:
            self.current_auth.finish(False, "locked")
        if self.window:
            self.window.close()
        config.log_event("lock", "PC locked")
        self.lockscreen = LockScreen(self, self._on_unlock)

    def _on_unlock(self, method):
        self.locked = False
        self.lockscreen = None
        config.LOCK_MARKER.unlink(missing_ok=True)
        config.log_event("unlock", f"Unlocked PC with {'Face ID' if method == 'face' else 'PIN'}")
        self.presence.last_seen = time.monotonic()
        QTimer.singleShot(300, self._next_auth)

    def _on_away(self):
        if self.locked or self.paused:
            return
        config.log_event("lock", "Locked automatically (you walked away)")
        if self.cfg["away_action"] == "windows":
            winapi.lock_workstation()
        else:
            self.lock_system()

    def _on_windows_unlock(self):
        if self.cfg["lock_after_windows_unlock"] and self.engine.enrolled and not self.paused:
            QTimer.singleShot(300, self.lock_system)

    # --- dashboard actions ---------------------------------------------------------
    def open_settings(self):
        if self.locked:
            return
        if self.onboarding or not self.engine.enrolled and not self.cfg.has_pin:
            self.show_onboarding()
            return
        if self.window:
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
            return
        self.authenticate("Open FaceLock", "Confirm it's you to change settings",
                          lambda ok: ok and self._show_window())

    def _show_window(self):
        if not self.window:
            self.window = MainWindow(self)
            self.window.setAttribute(Qt.WA_DeleteOnClose)
            self.window.destroyed.connect(lambda: setattr(self, "window", None))
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def test_face(self):
        def result(ok):
            QMessageBox.information(self.window, "FaceLock", "Recognized you!" if ok else "Not verified.")
        self.authenticate("Test Face ID", "Look at the camera", result)

    def add_locked_app(self, exe, path=""):
        exe = exe.lower()
        apps = list(self.cfg["locked_apps"])
        if exe not in apps:
            apps.append(exe)
            self.cfg["locked_apps"] = apps
        if path:
            self.cfg["app_paths"] = {**self.cfg["app_paths"], exe: path}
        config.log_event("system", f"{pretty_app(exe)} is now locked")

    def remove_locked_app(self, exe):
        self.cfg["locked_apps"] = [a for a in self.cfg["locked_apps"] if a != exe]
        config.log_event("system", f"{pretty_app(exe)} is no longer locked")

    def set_camera(self, index):
        self.cfg["camera_index"] = index
        self.hub.set_index(index)

    def pause_hotkey(self, recording):
        if recording and self.hotkey:
            self.hotkey.stop()
            self.hotkey.join(1)
            self.hotkey = None
        elif not recording and not self.hotkey:
            self.set_hotkey(self.cfg["hotkey"])

    def set_hotkey(self, text):
        try:
            new = winapi.HotkeyThread(text, lambda: self.call(self.lock_system))
        except ValueError as e:
            return str(e)
        old = self.hotkey
        if old:
            old.stop()
            old.join(1)
        if not new.start_and_wait():
            self.hotkey = None
            if old or text != self.cfg["hotkey"]:
                fallback = winapi.HotkeyThread(self.cfg["hotkey"], lambda: self.call(self.lock_system))
                if fallback.start_and_wait():
                    self.hotkey = fallback
            return "That shortcut is already used by another app."
        self.hotkey = new
        self.cfg["hotkey"] = text.strip().lower()
        return None

    def restart_as_admin(self):
        config.QUIT_FLAG.touch()
        if winapi.relaunch_as_admin(config.launch_args("--replace")):
            self.shutdown()
        else:
            config.QUIT_FLAG.unlink(missing_ok=True)

    def reset_everything(self):
        self.engine.delete_templates()
        config.clear_events()
        config.set_start_with_windows(False)
        config.CONFIG_PATH.unlink(missing_ok=True)
        self.shutdown(restart_args=[])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="FaceLock")
    parser.add_argument("--startup", action="store_true", help="launched at Windows sign-in")
    parser.add_argument("--lock", action="store_true", help="lock immediately")
    parser.add_argument("--replace", action="store_true", help="wait for a previous instance to exit")
    args = parser.parse_args(argv)

    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=config.APP_DIR / "facelock.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("FaceLock")
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setStyle("Fusion")
    qapp.setWindowIcon(QIcon(theme.logo_pixmap(64)))
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FaceLock.App")

    if not winapi.acquire_single_instance(wait_seconds=8 if args.replace else 0):
        if args.lock:
            winapi.send_lock_request()  # ask the running instance to lock
            return 0
        QMessageBox.information(None, "FaceLock", "FaceLock is already running. Look for it in the system tray.")
        return 0
    if not (config.DETECTOR_MODEL.exists() and config.RECOGNIZER_MODEL.exists()):
        QMessageBox.critical(None, "FaceLock", f"Face models are missing from:\n{config.MODELS_DIR}")
        return 1

    app = FaceLockApp(qapp, args)
    app.start()
    return qapp.exec()
