"""Paths, settings, PIN hashing and DPAPI-protected face storage."""

import ctypes
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import winreg
from ctypes import wintypes
from pathlib import Path

APP_NAME = "FaceLock"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONFIG_PATH = APP_DIR / "config.json"
FACES_PATH = APP_DIR / "faces.bin"
LOCK_MARKER = APP_DIR / "locked.flag"
INTRUDER_DIR = APP_DIR / "intruders"
HISTORY_PATH = APP_DIR / "history.jsonl"
QUIT_FLAG = APP_DIR / "quit.flag"
WATCHDOG_PID = APP_DIR / "watchdog.pid"

if getattr(sys, "frozen", False):
    MODELS_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "models"
else:
    MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

DETECTOR_MODEL = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = MODELS_DIR / "face_recognition_sface_2021dec.onnx"

DEFAULTS = {
    "camera_index": 0,
    "match_threshold": 0.45,      # SFace cosine similarity; 0.363 is OpenCV's minimum
    "require_liveness": True,     # head-turn challenge to resist photos
    "app_lock_enabled": True,
    "locked_apps": [],            # lowercase exe names, e.g. "discord.exe"
    "relock_grace_minutes": 0,    # keep an app unlocked this long after it closes
    "hotkey": "ctrl+alt+l",
    "auto_lock_away": False,
    "away_seconds": 60,
    "away_action": "overlay",     # "overlay" (FaceLock screen) or "windows" (Win lock screen)
    "lock_on_start": False,
    "start_with_windows": False,
    "save_intruders": True,
    "app_paths": {},              # exe name -> full path, used for icons
    "lock_after_windows_unlock": False,
    "low_light_boost": True,
    "lock_screen_blur": True,
    "self_protection": True,      # watchdog restarts FaceLock if it is killed
    "theme": "system",            # "system" | "dark" | "light"
    "onboarded": False,
    "pin_hash": None,
    "pin_salt": None,
}


class Config:
    def __init__(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                self._data.update(json.loads(CONFIG_PATH.read_text("utf-8")))
            except (OSError, ValueError):
                pass

    def __getitem__(self, key):
        with self._lock:
            return self._data[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._data[key] = value
            self.save()

    def save(self):
        with self._lock:
            tmp = CONFIG_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), "utf-8")
            os.replace(tmp, CONFIG_PATH)

    # --- PIN -------------------------------------------------------------
    @property
    def has_pin(self):
        return bool(self["pin_hash"])

    def set_pin(self, pin):
        salt = secrets.token_bytes(16)
        with self._lock:
            self._data["pin_salt"] = salt.hex()
            self._data["pin_hash"] = _hash_pin(pin, salt).hex()
            self.save()

    def check_pin(self, pin):
        if not self.has_pin:
            return False
        salt = bytes.fromhex(self["pin_salt"])
        return hmac.compare_digest(_hash_pin(pin, salt).hex(), self["pin_hash"])


def _hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 300_000)


# --- Activity history ----------------------------------------------------------

_history_lock = threading.Lock()


def log_event(kind, text, ok=True, **extra):
    """kind: unlock | app | lock | intruder | pin | system"""
    import time
    entry = {"t": time.time(), "kind": kind, "text": text, "ok": ok, **extra}
    with _history_lock:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        try:
            if HISTORY_PATH.stat().st_size > 512_000:
                lines = HISTORY_PATH.read_text("utf-8").splitlines()[-1500:]
                HISTORY_PATH.write_text("\n".join(lines) + "\n", "utf-8")
        except OSError:
            pass


def read_events(limit=300):
    with _history_lock:
        if not HISTORY_PATH.exists():
            return []
        lines = HISTORY_PATH.read_text("utf-8").splitlines()[-limit:]
    events = []
    for line in reversed(lines):
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events


def clear_events():
    with _history_lock:
        HISTORY_PATH.unlink(missing_ok=True)


# --- DPAPI: face templates are encrypted to the current Windows user ------

class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_ENTROPY = b"FaceLock-face-templates-v1"


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(data, protect):
    blob_in, _keep1 = _blob(data)
    entropy, _keep2 = _blob(_ENTROPY)
    blob_out = _DataBlob()
    fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
    args = (ctypes.byref(blob_in), "FaceLock" if protect else None, ctypes.byref(entropy),
            None, None, 0x1, ctypes.byref(blob_out))  # 0x1 = CRYPTPROTECT_UI_FORBIDDEN
    if not fn(*args):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def save_faces(embeddings):
    import numpy as np
    raw = np.asarray(embeddings, dtype=np.float32).tobytes()
    FACES_PATH.write_bytes(_dpapi(raw, protect=True))


def load_faces():
    import numpy as np
    if not FACES_PATH.exists():
        return np.zeros((0, 128), np.float32)
    try:
        raw = _dpapi(FACES_PATH.read_bytes(), protect=False)
    except OSError:
        return np.zeros((0, 128), np.float32)
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 128).copy()


# --- Start with Windows -----------------------------------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_args(*args):
    """argv that starts FaceLock again (frozen exe or pythonw + script)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return [str(exe), str(Path(__file__).resolve().parent.parent / "FaceLock.pyw"), *args]


def launch_command():
    return " ".join(f'"{a}"' for a in launch_args("--startup"))


def set_start_with_windows(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
