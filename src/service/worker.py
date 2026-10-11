"""CAN owner thread, with cooperative scan cancellation and a jog lease."""

import logging
import queue
import threading
import time
from dataclasses import asdict
from typing import Any

from PySide6.QtCore import QThread, Signal

from service.motor import MotorService
from transport.can_transport import CanTransport, GsUsbTransport, SocketCanTransport
from transport.fake import FakeCanTransport


class CanWorker(QThread):
    result = Signal(str, object)
    error = Signal(str)
    frame = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.commands: queue.Queue[tuple[str, tuple]] = queue.Queue()
        self.cancel = threading.Event()
        self.exiting = threading.Event()
        self.disconnect_requested = threading.Event()
        self.stop_requested = threading.Event()
        self.stop_target: int | None = None
        self.lease_lock = threading.Lock()
        self.lease_until = 0.0
        self.service = MotorService(sink=self.frame.emit)
        self.selected: int | None = None

    def submit(self, command: str, *args) -> None:
        if command in ("disconnect", "exit", "stop"):
            self.cancel.set()
        if command == "disconnect":
            self.disconnect_requested.set()
        elif command == "exit":
            self.exiting.set()
        elif command == "stop":
            self.stop_target = args[0]
            self.stop_requested.set()
            with self.lease_lock:
                self.lease_until = 0
        elif command == "jog_release":
            with self.lease_lock:
                self.lease_until = 0
        self.commands.put((command, args))

    def still_held(self) -> bool:
        with self.lease_lock:
            return (
                time.monotonic() < self.lease_until
                and not self.exiting.is_set()
                and not self.disconnect_requested.is_set()
                and not self.stop_requested.is_set()
            )

    def heartbeat(self) -> None:
        with self.lease_lock:
            self.lease_until = time.monotonic() + 0.35

    def drain(self) -> None:
        while True:
            try:
                self.commands.get_nowait()
            except queue.Empty:
                return

    def run(self) -> None:
        last_poll = 0.0
        try:
            while not self.exiting.is_set():
                if self.disconnect_requested.is_set():
                    self.drain()
                    errors = self.service.disconnect()
                    self.selected = None
                    self.disconnect_requested.clear()
                    self.cancel.clear()
                    self.result.emit("disconnect", errors)
                    continue
                try:
                    if self.stop_requested.is_set():
                        self.drain()
                        if self.stop_target in self.service.motors:
                            self.service.stop(self.stop_target)
                        self.stop_requested.clear()
                        self.cancel.clear()
                        self.result.emit("stop", None)
                        continue
                    with self.lease_lock:
                        expired = time.monotonic() >= self.lease_until
                    if self.service.jogging is not None and expired:
                        self.service.stop(self.service.jogging)
                        self.result.emit("stop", None)
                    try:
                        command, args = self.commands.get(timeout=0.03)
                    except queue.Empty:
                        command, args = "", ()
                    if (
                        self.exiting.is_set()
                        or self.disconnect_requested.is_set()
                        or self.stop_requested.is_set()
                    ):
                        continue
                    if command:
                        self.dispatch(command, args)
                    if (
                        self.selected is not None
                        and self.service.sdk is not None
                        and time.monotonic() - last_poll > 0.25
                    ):
                        self.result.emit("telemetry", self.service.telemetry(self.selected))
                        last_poll = time.monotonic()
                except Exception as exc:
                    logging.getLogger("rs02.worker").exception("CAN operation failed")
                    self.drain()
                    errors = self.service.disconnect()
                    self.selected = None
                    self.error.emit(
                        str(exc) + ("; cleanup: " + "; ".join(errors) if errors else "")
                    )
                    self.result.emit("disconnect", errors)
        finally:
            errors = self.service.application_exit()
            if errors:
                self.error.emit("Exit cleanup: " + "; ".join(errors))

    def dispatch(self, command: str, args: tuple) -> None:
        value: Any = None
        if command == "enumerate":
            # Discovery errors are setup diagnostics, not motor-operation exceptions.
            try:
                value = GsUsbTransport.enumerate_devices()
            except Exception as exc:
                self.error.emit(str(exc))
                value = []
        elif command == "connect":
            config, device = args
            config.validate()
            self.service.config = config
            if config.backend == "simulated":
                transport: CanTransport = FakeCanTransport()
            elif config.backend == "gs_usb":
                transport = GsUsbTransport(device, config.channel, config.bitrate)
            else:
                transport = SocketCanTransport(config.socketcan, config.bitrate)
            try:
                self.service.connect(transport)
            except Exception:
                transport.shutdown()
                raise
            value = asdict(config)
        elif command == "scan":
            self.cancel.clear()
            value = self.service.scan(cancel=self.cancel.is_set)
        elif command == "select":
            self.service.require(args[0])
            if self.service.enabled:
                raise RuntimeError("Disable before switching motors")
            self.selected = args[0]
        elif command == "configure":
            motor_id, config = args
            config.validate()
            self.service.config = config
            self.service.configure(motor_id)
        elif command == "export":
            self.service.log.export(args[0])
        elif command == "change_id":
            self.service.change_id(*args)
            self.selected = args[1]
            value = dict(self.service.motors)
        elif command in ("enable", "stop", "jog_press", "jog_release", "write", "read"):
            if command == "jog_press":
                if not self.still_held():
                    return
                self.service.jog_press(args[0], args[1], still_held=self.still_held)
            elif command == "enable":
                self.service.enable(
                    args[0],
                    permitted=lambda: not (
                        self.exiting.is_set()
                        or self.disconnect_requested.is_set()
                        or self.stop_requested.is_set()
                    ),
                )
                if args[0] not in self.service.enabled:
                    return
            else:
                value = getattr(self.service, command)(*args)
        elif command not in ("disconnect", "exit"):
            raise ValueError(f"Unknown command {command}")
        self.result.emit(command, value)
