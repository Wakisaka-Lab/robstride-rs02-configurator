"""Desktop UI: CAN I/O and enumeration never run on the GUI thread."""

import logging
import sys

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from protocol.sdk import PARAMETERS, WRITABLE
from service.config import Configuration
from service.logging import JsonFormatter
from service.worker import CanWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RobStride RS02 • Configurator")
        self.resize(1100, 780)
        self.connected = False
        self.running_motor = False
        self.busy = False
        self.held = False
        self.closing = False
        self.config = Configuration()
        self.worker = CanWorker(self)
        self.worker.result.connect(self.on_result)
        self.worker.error.connect(self.on_error)
        self.worker.frame.connect(self.on_frame)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel("RobStride RS02")
        title.setStyleSheet("font-size: 26px; font-weight: 600")
        layout.addWidget(title)
        self.banner = QLabel("SIMULATED MODE • Disconnected • Disabled")
        self.banner.setStyleSheet("background: #163f52; color: white; padding: 12px")
        layout.addWidget(self.banner)
        connection = QHBoxLayout()
        self.backend = QComboBox()
        self.backend.addItem("Simulated RS02", "simulated")
        self.backend.addItem("Physical USB2CAN-X2 / gs_usb", "gs_usb")
        self.backend.addItem("Physical Linux / SocketCAN", "socketcan")
        self.backend.currentIndexChanged.connect(self.update_controls)
        self.devices = QComboBox()
        self.enumerate_button = QPushButton("Enumerate USB")
        self.enumerate_button.clicked.connect(lambda: self.worker.submit("enumerate"))
        self.channel = QComboBox()
        self.channel.addItems(["CAN 0", "CAN 1"])
        self.bitrate = QComboBox()
        for rate in (1000000, 500000, 250000, 125000):
            self.bitrate.addItem(f"{rate:,} bit/s", rate)
        self.socketcan = QComboBox()
        self.socketcan.setEditable(True)
        self.socketcan.addItems(["can0", "can1"])
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connect_transport)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(lambda: self.safe_action("disconnect"))
        for w in (
            self.backend,
            self.devices,
            self.enumerate_button,
            self.channel,
            self.bitrate,
            self.socketcan,
            self.connect_button,
            self.disconnect_button,
        ):
            connection.addWidget(w)
        layout.addLayout(connection)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        split.addWidget(left)
        discovery = QGroupBox("Motor discovery")
        discovery_layout = QFormLayout(discovery)
        self.scan_button = QPushButton("Scan IDs 1–254")
        self.scan_button.clicked.connect(self.scan)
        self.motor = QComboBox()
        self.motor.currentIndexChanged.connect(self.select_motor)
        discovery_layout.addRow(self.scan_button)
        discovery_layout.addRow("Motor / UUID", self.motor)
        self.telemetry = QLabel(
            "State: unknown\nFaults: unknown\nPosition: — rad\nVelocity: — rad/s\nCurrent: — A\nTemperature: — °C"
        )
        self.telemetry.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        discovery_layout.addRow(self.telemetry)
        left_layout.addWidget(discovery)
        settings = QGroupBox("Conservative operating settings • apply while disabled")
        form = QFormLayout(settings)
        self.mode = QComboBox()
        for text, number in [
            ("MIT (0)", 0),
            ("Position (1)", 1),
            ("Velocity (2)", 2),
            ("Current (3)", 3),
        ]:
            self.mode.addItem(text, number)
        self.mode.setCurrentIndex(2)
        form.addRow("Run mode", self.mode)
        self.numeric: dict[str, QDoubleSpinBox] = {}
        for name, label, maximum in [
            ("current_limit", "Current limit (A)", 2),
            ("velocity_limit", "Velocity limit (rad/s)", 2),
            ("acceleration", "Acceleration (rad/s²)", 2),
            ("jog_velocity", "Jog speed (rad/s)", 0.5),
        ]:
            spin = QDoubleSpinBox()
            spin.setRange(0.01, maximum)
            spin.setSingleStep(0.05)
            spin.setValue(getattr(self.config, name))
            self.numeric[name] = spin
            form.addRow(label, spin)
        self.apply_button = QPushButton("Apply & verify settings")
        self.apply_button.clicked.connect(self.apply_settings)
        form.addRow(self.apply_button)
        left_layout.addWidget(settings)
        parameters = QGroupBox("Supported SDK parameters")
        pform = QFormLayout(parameters)
        self.parameter = QComboBox()
        for name in PARAMETERS:
            self.parameter.addItem(name + (" (read only)" if name not in WRITABLE else ""), name)
        self.parameter_value = QDoubleSpinBox()
        self.parameter_value.setRange(-100000, 100000)
        self.parameter_value.setDecimals(4)
        self.read_button = QPushButton("Read")
        self.read_button.clicked.connect(lambda: self.command("read", self.parameter.currentData()))
        self.write_button = QPushButton("Write & verify")
        self.write_button.clicked.connect(
            lambda: self.command(
                "write", self.parameter.currentData(), self.parameter_value.value()
            )
        )
        self.parameter.currentIndexChanged.connect(self.update_controls)
        pform.addRow(self.parameter, self.parameter_value)
        row = QHBoxLayout()
        row.addWidget(self.read_button)
        row.addWidget(self.write_button)
        pform.addRow(row)
        self.new_id = QSpinBox()
        self.new_id.setRange(1, 254)
        self.new_id.setValue(128)
        self.id_button = QPushButton("Change CAN ID…")
        self.id_button.clicked.connect(self.change_id)
        pform.addRow(self.new_id, self.id_button)
        left_layout.addWidget(parameters)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        split.addWidget(right)
        motion = QGroupBox("Motion • explicit enable required")
        m = QVBoxLayout(motion)
        self.enable_button = QPushButton("Enable motor")
        self.enable_button.clicked.connect(self.enable_motor)
        self.disable_button = QPushButton("Disable motor")
        self.disable_button.clicked.connect(lambda: self.safe_action("stop"))
        self.stop_button = QPushButton("STOP • zero velocity + disable")
        self.stop_button.setStyleSheet("background: #a52835; color: white; padding: 12px")
        self.stop_button.clicked.connect(lambda: self.safe_action("stop"))
        jogs = QHBoxLayout()
        self.jog_buttons = []
        for name, direction in [("Hold to jog reverse", -1), ("Hold to jog forward", 1)]:
            button = QPushButton(name)
            button.setAutoRepeat(False)
            button.pressed.connect(lambda d=direction: self.jog_press(d))
            button.released.connect(self.jog_release)
            self.jog_buttons.append(button)
            jogs.addWidget(button)
        m.addWidget(self.enable_button)
        m.addWidget(self.disable_button)
        m.addLayout(jogs)
        m.addWidget(self.stop_button)
        m.addWidget(
            QLabel(
                "Releasing jog requests zero speed.\nStop, loss of focus, or jog lease expiry also disables.\nSoftware cannot guarantee stopping after USB or power loss."
            )
        )
        right_layout.addWidget(motion)
        self.messages = QTextEdit()
        self.messages.setReadOnly(True)
        self.messages.document().setMaximumBlockCount(2000)
        right_layout.addWidget(QLabel("CAN TX / RX and operation log"))
        right_layout.addWidget(self.messages)
        files = QHBoxLayout()
        for text, callback in [
            ("Save JSON", self.save_config),
            ("Load JSON", self.load_config),
            ("Export CAN CSV", self.export_csv),
        ]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            files.addWidget(b)
        layout.addLayout(files)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.heartbeat)
        self.timer.start(75)
        self.update_controls()

    def motor_id(self) -> int | None:
        return self.motor.currentData()

    def read_config(self) -> Configuration:
        c = Configuration(
            backend=self.backend.currentData(),
            bitrate=self.bitrate.currentData(),
            channel=self.channel.currentIndex(),
            socketcan=self.socketcan.currentText(),
            mode=self.mode.currentData(),
            current_limit=self.numeric["current_limit"].value(),
            velocity_limit=self.numeric["velocity_limit"].value(),
            acceleration=self.numeric["acceleration"].value(),
            jog_velocity=self.numeric["jog_velocity"].value(),
        )
        c.validate()
        return c

    def update_controls(self, *_args) -> None:
        if not hasattr(self, "timer"):
            return
        discovered = self.connected and self.motor_id() is not None and not self.busy
        editable = discovered and not self.running_motor
        self.connect_button.setEnabled(not self.connected and not self.busy)
        self.disconnect_button.setEnabled(self.connected or self.busy)
        self.scan_button.setEnabled(self.connected and not self.running_motor and not self.busy)
        self.motor.setEnabled(self.connected and not self.running_motor and not self.busy)
        for w in (self.apply_button, self.id_button, self.mode, *self.numeric.values()):
            w.setEnabled(editable)
        self.read_button.setEnabled(discovered)
        self.write_button.setEnabled(editable and self.parameter.currentData() in WRITABLE)
        self.enable_button.setEnabled(editable)
        for w in (self.disable_button, self.stop_button):
            w.setEnabled(self.connected and self.motor_id() is not None)
        for w in self.jog_buttons:
            w.setEnabled(discovered and self.running_motor and self.mode.currentData() == 2)
        for w in (
            self.backend,
            self.bitrate,
            self.channel,
            self.socketcan,
            self.devices,
            self.enumerate_button,
        ):
            w.setEnabled(not self.connected and not self.busy)
        physical = self.backend.currentData() != "simulated"
        self.banner.setText(
            f"{'PHYSICAL DEVICE MODE • Hardware unverified' if physical else 'SIMULATED MODE • No physical motor'} • {'Connected' if self.connected else 'Disconnected'} • {'Enabled' if self.running_motor else 'Disabled / not enabled by app'}"
        )

    def connect_transport(self) -> None:
        try:
            self.config = self.read_config()
            device = self.devices.currentData()
            if self.config.backend == "gs_usb" and device is None:
                raise ValueError("Enumerate and select a compatible USB device first")
            self.busy = True
            self.update_controls()
            self.worker.submit("connect", self.config, device)
        except Exception as exc:
            self.on_error(str(exc))

    def scan(self) -> None:
        self.busy = True
        self.update_controls()
        self.worker.submit("scan")

    def select_motor(self) -> None:
        if self.motor_id() is not None:
            self.worker.submit("select", self.motor_id())
        self.update_controls()

    def command(self, name: str, *args) -> None:
        if self.motor_id() is not None:
            self.busy = True
            self.update_controls()
            self.worker.submit(name, self.motor_id(), *args)

    def apply_settings(self) -> None:
        try:
            self.config = self.read_config()
            self.command("configure", self.config)
        except Exception as exc:
            self.on_error(str(exc))

    def enable_motor(self) -> None:
        if (
            QMessageBox.warning(
                self,
                "Enable motor",
                "The motor may produce torque. Secure the motor, clear the shaft and have a power cutoff ready. Enable using the shown conservative settings?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            try:
                self.config = self.read_config()
                self.worker.submit("configure", self.motor_id(), self.config)
                self.command("enable")
            except Exception as exc:
                self.on_error(str(exc))

    def jog_press(self, direction: int) -> None:
        if self.motor_id() is None:
            return
        self.held = True
        self.worker.heartbeat()
        self.worker.submit("jog_press", self.motor_id(), direction)

    def jog_release(self) -> None:
        if self.held and self.motor_id() is not None:
            self.held = False
            self.worker.submit("jog_release", self.motor_id())

    def heartbeat(self) -> None:
        if self.held:
            if self.isActiveWindow():
                self.worker.heartbeat()
            else:
                self.safe_action("stop")

    def safe_action(self, command: str) -> None:
        self.held = False
        if command == "disconnect":
            self.worker.submit(command)
        elif self.motor_id() is not None:
            self.worker.submit(command, self.motor_id())

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate and getattr(self, "held", False):
            self.safe_action("stop")
        return super().event(event)

    def change_id(self) -> None:
        old = self.motor_id()
        new = self.new_id.value()
        if (
            QMessageBox.warning(
                self,
                "Confirm CAN ID change",
                f"Connect ONLY ONE motor. Disconnect all other motors physically.\nChange motor {old} to {new}?\nThis may persist in motor firmware.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.command("change_id", new, True)

    def fill_motors(self, motors: dict) -> None:
        self.motor.blockSignals(True)
        self.motor.clear()
        for motor_id, uuid in motors.items():
            self.motor.addItem(f"ID {motor_id} • {uuid}", motor_id)
        self.motor.blockSignals(False)
        self.select_motor()

    def on_result(self, command: str, value) -> None:
        if command == "enumerate":
            self.devices.clear()
            for device in value:
                self.devices.addItem(
                    f"{device['product']} • {device['serial']} • {device['bus']}:{device['address']}",
                    device,
                )
            if not value:
                self.messages.append(
                    "No compatible gs_usb USB device found. Simulation remains available."
                )
        elif command == "connect":
            self.connected = True
        elif command == "disconnect":
            self.connected = False
            self.running_motor = False
            self.held = False
            self.motor.clear()
            self.telemetry.setText("Disconnected • telemetry unavailable")
            if value:
                self.messages.append("Cleanup attempts failed: " + "; ".join(value))
        elif command in ("scan", "change_id"):
            self.fill_motors(value)
        elif command == "enable":
            self.running_motor = True
        elif command == "stop":
            self.running_motor = False
            self.held = False
        elif command == "read":
            self.parameter_value.setValue(value)
        elif command == "telemetry":
            self.running_motor = value["enabled"]
            self.telemetry.setText(
                f"ID: {value['id']}  UUID: {value['uuid']}\nState: {value['state']} (0 reset / 1 calibration / 2 running)\nFaults: {value['faults']:#x}\nPosition: {value['position']:.4f} rad\nVelocity: {value['velocity']:.4f} rad/s\nCurrent (iqf): {value['current']:.4f} A\nTemperature: {value['temperature']:.1f} °C"
            )
        if command not in ("telemetry", "jog_press", "jog_release", "select"):
            self.busy = False
            self.messages.append(f"{command}: completed")
        self.update_controls()

    def on_error(self, error: str) -> None:
        self.busy = False
        self.messages.append("ERROR: " + error)
        self.statusBar().showMessage(error)
        self.update_controls()

    def on_frame(self, record: dict) -> None:
        self.messages.append(
            f"{record['time']:.3f} {record['direction']} {record['id']} [{record['dlc']}] {record['data']}"
        )

    def save_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save configuration", "rs02.json", "JSON (*.json)"
        )
        if path:
            try:
                self.read_config().save(path)
            except Exception as exc:
                self.on_error(str(exc))

    def load_config(self) -> None:
        if self.connected or self.busy:
            self.on_error("Disconnect before loading configuration")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load configuration", "", "JSON (*.json)")
        if path:
            try:
                c = Configuration.load(path)
                self.backend.setCurrentIndex(self.backend.findData(c.backend))
                self.bitrate.setCurrentIndex(self.bitrate.findData(c.bitrate))
                self.channel.setCurrentIndex(c.channel)
                self.socketcan.setCurrentText(c.socketcan)
                self.mode.setCurrentIndex(self.mode.findData(c.mode))
                for key, spin in self.numeric.items():
                    spin.setValue(getattr(c, key))
                self.config = c
                self.update_controls()
            except Exception as exc:
                self.on_error(str(exc))

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CAN log", "can-log.csv", "CSV (*.csv)")
        if path:
            self.worker.submit("export", path)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker.isRunning():
            event.ignore()
            self.closing = True
            self.held = False
            self.timer.stop()
            self.centralWidget().setEnabled(False)
            self.banner.setText("Closing • requesting zero velocity, disable, and transport close…")
            self.worker.submit("exit")
        else:
            event.accept()

    def worker_finished(self) -> None:
        if self.closing:
            self.close()


def main() -> int:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    previous_hook = sys.excepthook

    def safe_exception_hook(exc_type, exc, traceback):
        window.held = False
        window.worker.submit("disconnect")
        logging.getLogger("rs02.gui").error(
            "Unhandled GUI exception", exc_info=(exc_type, exc, traceback)
        )
        window.on_error(f"Unhandled exception: {exc}; safety disconnect requested")

    sys.excepthook = safe_exception_hook

    def shutdown():
        window.worker.submit("exit")
        window.worker.wait()

    app.aboutToQuit.connect(shutdown)
    try:
        return app.exec()
    finally:
        shutdown()
        sys.excepthook = previous_hook


if __name__ == "__main__":
    raise SystemExit(main())
