"""Tiny companion process that restarts FaceLock if it is killed (e.g. from Task Manager).

It detaches from FaceLock's process tree so "End process tree" doesn't take it down too, and
FaceLock in turn restarts the watchdog if it disappears. A clean, authenticated quit writes
quit.flag so the watchdog knows to stand down.
"""

import os
import subprocess
import sys
import time

import psutil

from . import config

DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW


def spawn(main_pid):
    """Start a watchdog for main_pid (from the main app)."""
    subprocess.Popen(config.launch_args("--watchdog", str(main_pid)), creationflags=DETACHED, close_fds=True)


def watchdog_alive():
    try:
        pid = int(config.WATCHDOG_PID.read_text())
        p = psutil.Process(pid)
        return p.is_running() and "--watchdog-run" in " ".join(p.cmdline())
    except (OSError, ValueError, psutil.Error):
        return False


def run(argv):
    """Entry for `--watchdog <pid>` and `--watchdog-run <pid>`."""
    if argv[0] == "--watchdog":
        # Re-spawn once so our parent (FaceLock) is not our parent anymore.
        subprocess.Popen(config.launch_args("--watchdog-run", argv[1]), creationflags=DETACHED, close_fds=True)
        return
    main_pid = int(argv[1])
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    config.WATCHDOG_PID.write_text(str(os.getpid()))
    try:
        main = psutil.Process(main_pid)
    except psutil.Error:
        return
    while True:
        try:
            main.wait(timeout=1)
            break
        except psutil.TimeoutExpired:
            if config.QUIT_FLAG.exists():
                return
            try:
                if int(config.WATCHDOG_PID.read_text()) != os.getpid():
                    return  # a newer watchdog took over
            except (OSError, ValueError):
                pass
    time.sleep(0.5)
    if config.QUIT_FLAG.exists():
        return
    args = ["--lock"] if config.LOCK_MARKER.exists() else []
    config.log_event("system", "FaceLock was closed unexpectedly and restarted itself", ok=False)
    subprocess.Popen(config.launch_args(*args), creationflags=0x08000000, close_fds=True)


if __name__ == "__main__":
    run(sys.argv[1:])
