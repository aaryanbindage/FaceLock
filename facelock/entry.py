"""Process entry point. Keeps the watchdog path free of heavy imports (Qt, OpenCV)."""

import sys


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("--watchdog", "--watchdog-run"):
        from . import watchdog
        watchdog.run(argv)
        return 0
    from .app import main as app_main
    return app_main(argv)


if __name__ == "__main__":
    sys.exit(main())
