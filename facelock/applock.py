"""Watches for locked apps, freezes them, and asks for Face ID before letting them run."""

import os
import threading
import time

import psutil

POLL_SECONDS = 0.2
MIN_RELOCK_SECONDS = 3   # tolerate launchers that briefly exit and respawn


class AppLocker(threading.Thread):
    def __init__(self, cfg, request_auth, log=print, is_paused=lambda: False):
        """request_auth(exe_name, exe_path, callback) must be thread-safe; callback(ok: bool)."""
        super().__init__(daemon=True)
        self.cfg = cfg
        self.is_paused = is_paused
        self.request_auth = request_auth
        self.log = log
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._known = set()
        self.granted = {}    # exe -> last time a process of it was seen running
        self.pending = {}    # exe -> [psutil.Process] frozen while waiting for auth
        self.denied_access = set()  # exes we could not freeze (elevated processes)
        self._own_pid = os.getpid()

    def stop(self):
        self._stop.set()
        with self._lock:
            names = list(self.pending)
        for name in names:
            self.resolve(name, False)

    def run(self):
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception as e:  # never let the watcher die
                self.log(f"app lock scan error: {e}")
            time.sleep(POLL_SECONDS)

    def _scan(self):
        active = self.cfg["app_lock_enabled"] and not self.is_paused()
        locked = {a.lower() for a in self.cfg["locked_apps"]} if active else set()
        now = time.monotonic()
        running = {}
        for p in psutil.process_iter(["pid", "name"]):
            name = (p.info["name"] or "").lower()
            running[p.pid] = name
            if p.pid in self._known or p.pid == self._own_pid or name not in locked:
                continue
            with self._lock:
                if name in self.granted:
                    continue
                self._freeze(p, name)

        seen_names = set(running.values())
        grace = max(MIN_RELOCK_SECONDS, self.cfg["relock_grace_minutes"] * 60)
        with self._lock:
            for name in list(self.granted):
                if name in seen_names:
                    self.granted[name] = now
                elif now - self.granted[name] > grace or name not in locked:
                    del self.granted[name]
        self._known = set(running)

    def _freeze(self, proc, name):
        try:
            proc.suspend()
        except psutil.AccessDenied:
            if name not in self.denied_access:
                self.denied_access.add(name)
                self.log(f"Can't lock {name}: it runs as administrator. Run FaceLock as admin to lock it.")
            return
        except psutil.NoSuchProcess:
            return
        first = name not in self.pending
        self.pending.setdefault(name, []).append(proc)
        if first:
            try:
                path = proc.exe()
            except psutil.Error:
                path = ""
            self.request_auth(name, path, lambda ok, n=name: self.resolve(n, ok))

    def resolve(self, name, ok):
        with self._lock:
            procs = self.pending.pop(name, [])
            if ok:
                self.granted[name] = time.monotonic()
        for p in procs:
            try:
                p.resume() if ok else p.kill()
            except psutil.Error:
                pass

