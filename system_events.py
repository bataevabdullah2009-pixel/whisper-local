"""Suspend notifications plus a clock-gap fallback; no user activity is recorded."""
from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QObject, Signal, QTimer, QAbstractNativeEventFilter

LOG = logging.getLogger("WhisperLocal")


class PowerFilter(QAbstractNativeEventFilter):
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor

    def nativeEventFilter(self, event_type, message):
        import ctypes
        from ctypes import wintypes
        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if msg.message == 0x218:  # WM_POWERBROADCAST
            self.monitor.power_event(int(msg.wParam))
        return False, 0


class SystemEvents(QObject):
    interrupted = Signal()
    resumed = Signal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.suspended = False
        self.last_tick = (time.monotonic(), time.time())
        self.native_filter = None
        self.center = None
        self.observers = []
        self.blocks = []
        if sys.platform == "win32":
            self.native_filter = PowerFilter(self)
            app.installNativeEventFilter(self.native_filter)
        elif sys.platform == "darwin":
            import AppKit
            from Foundation import NSOperationQueue
            self.center = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()
            for name, block in ((AppKit.NSWorkspaceWillSleepNotification, lambda notification: self.suspend()),
                                (AppKit.NSWorkspaceDidWakeNotification, lambda notification: self.resume())):
                self.blocks.append(block)
                self.observers.append(self.center.addObserverForName_object_queue_usingBlock_(
                    name, None, NSOperationQueue.mainQueue(), block))
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def power_event(self, event):
        if event == 4:  # PBT_APMSUSPEND
            self.suspend()
        elif event in (7, 18):  # PBT_APMRESUMESUSPEND / PBT_APMRESUMEAUTOMATIC
            if self.suspended:
                self.resume()
            else:
                self.check()  # Also handles a missed suspend; ignore duplicate wake broadcasts.

    def suspend(self):
        if not self.suspended:
            self.suspended = True
            LOG.info("System suspend; invalidate pending dictation")
            self.interrupted.emit()

    def resume(self):
        self.last_tick = (time.monotonic(), time.time())
        if self.suspended:
            self.suspended = False
            self.resumed.emit()

    def check(self):
        now = (time.monotonic(), time.time())
        previous, self.last_tick = self.last_tick, now
        if self.suspended:
            return False
        # Check at result/paste boundaries too, before timers queued after wake run.
        # A long event-loop stall is treated conservatively just like sleep.
        if max(now[0] - previous[0], now[1] - previous[1]) > 5:
            self.suspend()
            self.resume()
            return False
        return True

    def close(self):
        self.timer.stop()
        if self.native_filter is not None:
            self.app.removeNativeEventFilter(self.native_filter)
            self.native_filter = None
        if self.center is not None:
            for observer in self.observers:
                self.center.removeObserver_(observer)
            self.observers.clear()
            self.blocks.clear()
            self.center = None
