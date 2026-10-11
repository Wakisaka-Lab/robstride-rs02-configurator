import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from gui.app import MainWindow
from service.config import Configuration
from service.worker import CanWorker
from transport.fake import FakeCanTransport


@pytest.fixture
def worker(qtbot):
    w = CanWorker()
    w.start()
    w.submit("connect", Configuration(), None)
    qtbot.waitUntil(lambda: w.service.sdk is not None)
    w.submit("scan")
    qtbot.waitUntil(lambda: 127 in w.service.motors)
    yield w
    w.submit("exit")
    assert w.wait(3000)


def test_worker_thread_and_exit_cleanup(worker, qtbot):
    fake = worker.service.sdk.channel_handler.inner
    worker.submit("enable", 127)
    qtbot.waitUntil(lambda: fake.motors[0].enabled)
    worker.heartbeat()
    worker.submit("jog_press", 127, 1)
    qtbot.waitUntil(lambda: fake.motors[0].parameters[0x700A] > 0)
    worker.submit("exit")
    assert worker.wait(3000)
    assert fake.closed and not fake.motors[0].enabled
    assert fake.motors[0].parameters[0x700A] == 0


def test_jog_lease_loss_disables(worker, qtbot):
    fake = worker.service.sdk.channel_handler.inner
    worker.submit("enable", 127)
    qtbot.waitUntil(lambda: fake.motors[0].enabled)
    worker.heartbeat()
    worker.submit("jog_press", 127, 1)
    qtbot.waitUntil(lambda: fake.motors[0].parameters[0x700A] > 0)
    qtbot.waitUntil(lambda: not fake.motors[0].enabled, timeout=2000)
    assert fake.motors[0].parameters[0x700A] == 0


def test_expired_press_never_moves(worker, qtbot):
    fake = worker.service.sdk.channel_handler.inner
    worker.submit("enable", 127)
    qtbot.waitUntil(lambda: fake.motors[0].enabled)
    worker.submit("jog_press", 127, 1)
    qtbot.wait(150)
    assert fake.motors[0].parameters[0x700A] == 0


def test_worker_exception_closes_and_disables(worker, qtbot, monkeypatch):
    fake = worker.service.sdk.channel_handler.inner
    original = fake.send

    def fail_read(msg):
        if msg.arbitration_id >> 24 == 17:
            raise RuntimeError("Injected read failure")
        return original(msg)

    worker.submit("enable", 127)
    qtbot.waitUntil(lambda: fake.motors[0].enabled)
    monkeypatch.setattr(fake, "send", fail_read)
    with qtbot.waitSignal(worker.error, timeout=2000):
        worker.submit("read", 127, "mode")
    qtbot.waitUntil(lambda: fake.closed)
    assert not fake.motors[0].enabled
    assert fake.motors[0].parameters[0x700A] == 0


def test_worker_can_io_is_not_gui_thread(qtbot, monkeypatch):
    thread_ids = []
    original = FakeCanTransport.send

    def record(self, message):
        thread_ids.append(threading.get_ident())
        return original(self, message)

    monkeypatch.setattr(FakeCanTransport, "send", record)
    w = CanWorker()
    w.start()
    try:
        w.submit("connect", Configuration(), None)
        w.submit("scan")
        qtbot.waitUntil(lambda: 127 in w.service.motors)
        assert thread_ids and set(thread_ids) != {threading.get_ident()}
        assert threading.get_ident() not in thread_ids
    finally:
        w.submit("exit")
        assert w.wait(3000)


def test_gui_full_simulated_flow_and_close(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert not window.connected and not window.enable_button.isEnabled()
    assert "SIMULATED" in window.banner.text()
    qtbot.mouseClick(window.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.connected)
    assert window.scan_button.isEnabled()
    qtbot.mouseClick(window.scan_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.motor_id() == 127 and not window.busy)
    fake = window.worker.service.sdk.channel_handler.inner
    assert not fake.motors[0].enabled
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(window.enable_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.running_motor)
    qtbot.waitUntil(lambda: "Temperature" in window.telemetry.text())
    # Offscreen platform has no reliable native active-window state.
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    button = window.jog_buttons[1]
    qtbot.mousePress(button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: fake.motors[0].parameters[0x700A] > 0)
    qtbot.mouseRelease(button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: fake.motors[0].parameters[0x700A] == 0)
    window.close()
    qtbot.waitUntil(lambda: not window.worker.isRunning())
    assert fake.closed and not fake.motors[0].enabled


def test_gui_id_cancel_sends_no_change(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.connect_transport()
        qtbot.waitUntil(lambda: window.connected)
        window.scan()
        qtbot.waitUntil(lambda: window.motor_id() == 127 and not window.busy)
        fake = window.worker.service.sdk.channel_handler.inner
        monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.No)
        window.change_id()
        qtbot.wait(100)
        assert not any(m.arbitration_id >> 24 == 7 for m in fake.sent)
        assert fake.motors[0].motor_id == 127
    finally:
        window.close()
        qtbot.waitUntil(lambda: not window.worker.isRunning())


def test_stop_preempts_queued_motion(qtbot):
    worker = CanWorker()
    worker.service.connect(FakeCanTransport())
    worker.service.scan([127])
    fake = worker.service.sdk.channel_handler.inner
    # Queue a configure/enable/jog before the worker starts; STOP must discard them.
    worker.submit("enable", 127)
    worker.heartbeat()
    worker.submit("jog_press", 127, 1)
    worker.submit("stop", 127)
    worker.start()
    try:
        qtbot.waitUntil(lambda: not worker.stop_requested.is_set())
        qtbot.wait(100)
        assert not fake.motors[0].enabled
        assert fake.motors[0].parameters[0x700A] == 0
        assert not any(m.arbitration_id >> 24 == 3 for m in fake.sent)
    finally:
        worker.submit("exit")
        assert worker.wait(3000)


def test_disconnect_preempts_queued_enable(qtbot):
    worker = CanWorker()
    worker.service.connect(FakeCanTransport())
    worker.service.scan([127])
    fake = worker.service.sdk.channel_handler.inner
    worker.submit("enable", 127)
    worker.submit("disconnect")
    worker.start()
    try:
        qtbot.waitUntil(lambda: fake.closed)
        assert not fake.motors[0].enabled
        assert not any(m.arbitration_id >> 24 == 3 for m in fake.sent)
    finally:
        worker.submit("exit")
        assert worker.wait(3000)


def test_release_during_jog_preflight_never_sends_motion(worker, qtbot, monkeypatch):
    fake = worker.service.sdk.channel_handler.inner
    worker.submit("enable", 127)
    qtbot.waitUntil(lambda: fake.motors[0].enabled)
    read_entered = threading.Event()
    read_resume = threading.Event()
    original = worker.service.read

    def delayed_read(motor_id, parameter):
        if parameter == "mode":
            read_entered.set()
            assert read_resume.wait(2)
        return original(motor_id, parameter)

    monkeypatch.setattr(worker.service, "read", delayed_read)
    fake.sent.clear()
    worker.heartbeat()
    worker.submit("jog_press", 127, 1)
    qtbot.waitUntil(read_entered.is_set)
    worker.submit("jog_release", 127)
    read_resume.set()
    qtbot.waitUntil(lambda: not worker.commands.qsize())
    qtbot.wait(50)
    assert fake.motors[0].parameters[0x700A] == 0
    velocity_writes = [
        m for m in fake.sent if m.arbitration_id >> 24 == 18 and m.data[:2] == b"\x0a\x70"
    ]
    assert all(bytes(m.data[4:]) == bytes(4) for m in velocity_writes)


@pytest.mark.parametrize("safety_command", ["stop", "disconnect", "exit"])
def test_enable_canceled_during_configuration(worker, qtbot, monkeypatch, safety_command):
    fake = worker.service.sdk.channel_handler.inner
    entered = threading.Event()
    resume = threading.Event()
    original = worker.service.configure

    def blocked_configure(motor_id):
        entered.set()
        assert resume.wait(2)
        original(motor_id)

    monkeypatch.setattr(worker.service, "configure", blocked_configure)
    fake.sent.clear()
    worker.submit("enable", 127)
    qtbot.waitUntil(entered.is_set)
    worker.submit(safety_command, *([127] if safety_command == "stop" else []))
    resume.set()
    if safety_command == "exit":
        assert worker.wait(3000)
    elif safety_command == "disconnect":
        qtbot.waitUntil(lambda: fake.closed)
    else:
        qtbot.waitUntil(lambda: not worker.stop_requested.is_set())
    assert not fake.motors[0].enabled
    assert not any(m.arbitration_id >> 24 == 3 for m in fake.sent)
