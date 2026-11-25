"""
Process-wide logging and crash reporting. Call install() once, early in main(),
before the QApplication is created. Nothing happens on import, so tests and
tools can import the widget without opening crash.log or replacing hooks.
"""

import faulthandler
import logging
import os
import sys
import threading

from PySide6 import QtCore

_crash_log = None


def _qt_handler(mode, ctx, msg):
    """Route Qt's own warnings (which often precede a crash) into the log."""
    logging.warning("Qt[%s] %s (%s:%s)", mode.name, msg, ctx.file, ctx.line)


def install(level: int = logging.DEBUG, crash_log_path: str = "crash.log") -> None:
    global _crash_log
    logging.basicConfig(level=level)

    # Dumps the Python stack of ALL threads on SIGSEGV/SIGABRT/etc.
    if _crash_log is None:
        _crash_log = open(crash_log_path, "a", buffering=1)
        faulthandler.enable(file=_crash_log, all_threads=True)
    # Optional: dump all stacks every 60s, which helps if it hangs rather than crashes
    # faulthandler.dump_traceback_later(60, repeat=True, file=_crash_log)

    # Make sure uncaught exceptions in any thread are logged
    sys.excepthook = lambda *a: logging.critical("Uncaught", exc_info=a)
    threading.excepthook = lambda args: logging.critical(
        "Uncaught in thread %s", args.thread, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    QtCore.qInstallMessageHandler(_qt_handler)

    # Silence Qt's debug categories (set "qt.multimedia.*=true" here for verbose multimedia logs)
    os.environ["QT_LOGGING_RULES"] = "*.debug=false"
