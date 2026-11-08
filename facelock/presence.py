"""Auto-lock when you walk away (or someone else sits down)."""

import threading
import time

CHECK_SECONDS = 1.5


class PresenceMonitor(threading.Thread):
    def __init__(self, cfg, engine, hub, is_locked, on_away):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.engine = engine
        self.hub = hub
        self.is_locked = is_locked
        self.on_away = on_away
        self._stop = threading.Event()
        self._holding = False
        self.last_seen = time.monotonic()

    def stop(self):
        self._stop.set()

    def _hold(self, want):
        if want and not self._holding:
            self._holding = self.hub.acquire()
        elif not want and self._holding:
            self.hub.release()
            self._holding = False

    def run(self):
        while not self._stop.is_set():
            active = self.cfg["auto_lock_away"] and self.engine.enrolled and not self.is_locked()
            self._hold(active)
            if not active:
                self.last_seen = time.monotonic()
                time.sleep(CHECK_SECONDS)
                continue
            frame = self.hub.read()
            if frame is not None and self.engine.is_owner(frame):
                self.last_seen = time.monotonic()
            elif time.monotonic() - self.last_seen > self.cfg["away_seconds"]:
                self.last_seen = time.monotonic()
                self.on_away()
            time.sleep(CHECK_SECONDS)
        self._hold(False)
