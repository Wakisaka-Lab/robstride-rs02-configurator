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


def confirm_japanese(parent, title: str, message: str, _buttons=None, _default=None):
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    yes = box.addButton("実行する", QMessageBox.ButtonRole.YesRole)
    no = box.addButton("キャンセル", QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    box.exec()
    return (
        QMessageBox.StandardButton.Yes
        if box.clickedButton() == yes
        else QMessageBox.StandardButton.No
    )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RobStride RS02 • モーター設定ツール")
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
        self.banner = QLabel("シミュレーション（SIMULATED MODE）• 未接続 • 無効")
        self.banner.setStyleSheet("background: #163f52; color: white; padding: 12px")
        layout.addWidget(self.banner)
        connection = QHBoxLayout()
        self.backend = QComboBox()
        self.backend.addItem("シミュレーション：RS02", "simulated")
        self.backend.addItem("実機：USB2CAN-X2 / gs_usb", "gs_usb")
        self.backend.addItem("実機：Linux / SocketCAN", "socketcan")
        self.backend.currentIndexChanged.connect(self.update_controls)
        self.devices = QComboBox()
        self.enumerate_button = QPushButton("USBを検索")
        self.enumerate_button.clicked.connect(lambda: self.worker.submit("enumerate"))
        self.channel = QComboBox()
        self.channel.addItems(["CAN 0", "CAN 1"])
        self.bitrate = QComboBox()
        for rate in (1000000, 500000, 250000, 125000):
            self.bitrate.addItem(f"{rate:,} bit/s", rate)
        self.socketcan = QComboBox()
        self.socketcan.setEditable(True)
        self.socketcan.addItems(["can0", "can1"])
        self.connect_button = QPushButton("接続")
        self.connect_button.clicked.connect(self.connect_transport)
        self.disconnect_button = QPushButton("切断")
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
        discovery = QGroupBox("モーターの検索")
        discovery_layout = QFormLayout(discovery)
        self.scan_button = QPushButton("モーターを検索（ID 1～254）")
        self.scan_button.clicked.connect(self.scan)
        self.motor = QComboBox()
        self.motor.currentIndexChanged.connect(self.select_motor)
        discovery_layout.addRow(self.scan_button)
        discovery_layout.addRow("モーター / UUID", self.motor)
        self.telemetry = QLabel(
            "状態：不明\n異常：不明\n位置：— rad\n速度：— rad/s\n電流：— A\n温度：— °C"
        )
        self.telemetry.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        discovery_layout.addRow(self.telemetry)
        left_layout.addWidget(discovery)
        settings = QGroupBox("動作設定 • モーターを無効にして適用")
        form = QFormLayout(settings)
        self.mode = QComboBox()
        for text, number in [
            ("MIT (0)", 0),
            ("位置モード (1)", 1),
            ("速度モード (2)", 2),
            ("電流モード (3)", 3),
        ]:
            self.mode.addItem(text, number)
        self.mode.setCurrentIndex(2)
        form.addRow("動作モード", self.mode)
        self.numeric: dict[str, QDoubleSpinBox] = {}
        for name, label, maximum in [
            ("current_limit", "電流上限 (A)", 2),
            ("velocity_limit", "速度上限 (rad/s)", 2),
            ("acceleration", "加速度 (rad/s²)", 2),
            ("jog_velocity", "ジョグ速度 (rad/s)", 0.5),
        ]:
            spin = QDoubleSpinBox()
            spin.setRange(0.01, maximum)
            spin.setSingleStep(0.05)
            spin.setValue(getattr(self.config, name))
            self.numeric[name] = spin
            form.addRow(label, spin)
        self.apply_button = QPushButton("設定を適用・読み戻し確認")
        self.apply_button.clicked.connect(self.apply_settings)
        form.addRow(self.apply_button)
        left_layout.addWidget(settings)
        parameters = QGroupBox("対応パラメーター")
        pform = QFormLayout(parameters)
        self.parameter = QComboBox()
        labels = {
            "mode": "動作モード",
            "current_limit": "電流上限",
            "velocity_limit": "速度上限",
            "acceleration": "加速度",
            "velocity_target": "速度目標",
            "position": "位置",
            "velocity": "速度",
            "current": "電流 (iqf)",
            "bus_voltage": "電源電圧",
        }
        for name in PARAMETERS:
            self.parameter.addItem(
                labels[name] + ("（読み取り専用）" if name not in WRITABLE else ""), name
            )
        self.parameter_value = QDoubleSpinBox()
        self.parameter_value.setRange(-100000, 100000)
        self.parameter_value.setDecimals(4)
        self.read_button = QPushButton("読み取り")
        self.read_button.clicked.connect(lambda: self.command("read", self.parameter.currentData()))
        self.write_button = QPushButton("書き込み・確認")
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
        self.id_button = QPushButton("CAN IDを変更…")
        self.id_button.clicked.connect(self.change_id)
        pform.addRow(self.new_id, self.id_button)
        left_layout.addWidget(parameters)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        split.addWidget(right)
        motion = QGroupBox("動作操作 • 有効化が必要")
        m = QVBoxLayout(motion)
        self.enable_button = QPushButton("モーターを有効化")
        self.enable_button.clicked.connect(self.enable_motor)
        self.disable_button = QPushButton("モーターを無効化")
        self.disable_button.clicked.connect(lambda: self.safe_action("stop"))
        self.stop_button = QPushButton("停止 • 速度ゼロ要求＋無効化")
        self.stop_button.setStyleSheet("background: #a52835; color: white; padding: 12px")
        self.stop_button.clicked.connect(lambda: self.safe_action("stop"))
        jogs = QHBoxLayout()
        self.jog_buttons = []
        for name, direction in [("押している間だけ逆転", -1), ("押している間だけ正転", 1)]:
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
                "ボタンを離すと速度ゼロを要求します。\n停止・フォーカス喪失・継続通知の途絶で無効化も試みます。\nUSB切断や電源喪失時の停止は保証できません。"
            )
        )
        right_layout.addWidget(motion)
        self.messages = QTextEdit()
        self.messages.setReadOnly(True)
        self.messages.document().setMaximumBlockCount(2000)
        right_layout.addWidget(QLabel("CAN送受信（TX / RX）・操作ログ"))
        right_layout.addWidget(self.messages)
        files = QHBoxLayout()
        for text, callback in [
            ("設定を保存（JSON）", self.save_config),
            ("設定を読み込む（JSON）", self.load_config),
            ("CANログを出力（CSV）", self.export_csv),
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
            f"{'実機モード（PHYSICAL DEVICE MODE）• 実機未検証' if physical else 'シミュレーション（SIMULATED MODE）• 実機には通信しません'} • {'接続中' if self.connected else '未接続'} • {'有効' if self.running_motor else '無効／このアプリからは有効化していません'}"
        )

    def connect_transport(self) -> None:
        try:
            self.config = self.read_config()
            device = self.devices.currentData()
            if self.config.backend == "gs_usb" and device is None:
                raise ValueError("先に「USBを検索」で対応デバイスを選択してください")
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
            confirm_japanese(
                self,
                "モーターを有効化",
                "モーターがトルクを発生する可能性があります。モーターを固定し、軸の周囲を空け、電源を遮断できるようにしてください。表示中の設定で有効化しますか？",
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
            confirm_japanese(
                self,
                "CAN ID変更の確認",
                f"接続するモーターは必ず1台だけにしてください。他のモーターは物理的に取り外してください。\nCAN IDを {old} から {new} に変更しますか？\n変更がモーターに保存される場合があります。",
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
                    "対応するgs_usb USBデバイスが見つかりません。シミュレーションは利用できます。"
                )
        elif command == "connect":
            self.connected = True
        elif command == "disconnect":
            self.connected = False
            self.running_motor = False
            self.held = False
            self.motor.clear()
            self.telemetry.setText("未接続 • 測定値は取得できません")
            if value:
                self.messages.append("停止・無効化・切断の試行に失敗：" + "; ".join(value))
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
                f"ID: {value['id']}  UUID: {value['uuid']}\n状態：{value['state']}（0 リセット / 1 校正 / 2 動作中）\n異常：{value['faults']:#x}\n位置：{value['position']:.4f} rad\n速度：{value['velocity']:.4f} rad/s\n電流 (iqf)：{value['current']:.4f} A\n温度：{value['temperature']:.1f} °C"
            )
        if command not in ("telemetry", "jog_press", "jog_release", "select"):
            self.busy = False
            labels = {
                "enumerate": "USB検索",
                "connect": "接続",
                "disconnect": "切断",
                "scan": "モーター検索",
                "change_id": "CAN ID変更",
                "configure": "設定適用",
                "enable": "有効化",
                "stop": "停止・無効化",
                "read": "読み取り",
                "write": "書き込み・確認",
                "export": "CSV出力",
            }
            self.messages.append(f"{labels.get(command, command)}：完了")
        self.update_controls()

    def on_error(self, error: str) -> None:
        self.busy = False
        self.messages.append("エラー：" + error)
        self.statusBar().showMessage(error)
        self.update_controls()

    def on_frame(self, record: dict) -> None:
        self.messages.append(
            f"{record['time']:.3f} {record['direction']} {record['id']} [{record['dlc']}] {record['data']}"
        )

    def save_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "設定を保存", "rs02.json", "JSON (*.json)")
        if path:
            try:
                self.read_config().save(path)
            except Exception as exc:
                self.on_error(str(exc))

    def load_config(self) -> None:
        if self.connected or self.busy:
            self.on_error("設定を読み込む前に切断してください")
            return
        path, _ = QFileDialog.getOpenFileName(self, "設定を読み込み", "", "JSON (*.json)")
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
        path, _ = QFileDialog.getSaveFileName(self, "CANログを出力", "can-log.csv", "CSV (*.csv)")
        if path:
            self.worker.submit("export", path)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker.isRunning():
            event.ignore()
            self.closing = True
            self.held = False
            self.timer.stop()
            self.centralWidget().setEnabled(False)
            self.banner.setText("終了処理中 • 速度ゼロ・無効化を要求して通信を閉じています…")
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
        window.on_error(f"未処理の例外：{exc}；安全のため切断を要求しました")

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
