import csv
import json
import struct

import pytest
from robstride_dynamics.protocol import CommunicationType as C

from protocol.sdk import PARAMETERS, SdkAdapter
from service.config import Configuration
from service.motor import MotorService
from transport.fake import FakeCanTransport, SimulatedRS02


@pytest.fixture
def setup_motor():
    fake = FakeCanTransport()
    service = MotorService()
    service.connect(fake)
    service.scan([126, 127, 128])
    yield service, fake, fake.motors[0]
    service.disconnect()


def test_disconnected_and_disabled():
    service = MotorService()
    assert service.sdk is None and not service.enabled
    with pytest.raises(RuntimeError):
        service.enable(127)


def test_scan_and_safe_discovery(setup_motor):
    service, fake, motor = setup_motor
    assert service.motors == {127: motor.uuid.hex()}
    assert not motor.enabled
    assert all(msg.is_extended_id and not msg.is_fd and msg.dlc == 8 for msg in fake.sent)
    assert not any(msg.arbitration_id >> 24 == C.ENABLE for msg in fake.sent)


@pytest.mark.parametrize(
    "name,value",
    [("mode", 2), ("current_limit", 0.7), ("velocity_limit", 0.3), ("acceleration", 0.2)],
)
def test_parameters(setup_motor, name, value):
    service, _, _ = setup_motor
    service.write(127, name, value)
    assert service.read(127, name) == pytest.approx(value)


@pytest.mark.parametrize(
    "parameter,value",
    [("current_limit", 100), ("mode", 1.5), ("velocity_limit", float("nan")), ("position", 1)],
)
def test_reject_unsafe_writes(setup_motor, parameter, value):
    service, _, _ = setup_motor
    with pytest.raises(ValueError):
        service.write(127, parameter, value)


def test_enable_disable_and_jog(setup_motor):
    service, _, motor = setup_motor
    with pytest.raises(RuntimeError):
        service.jog_press(127, 1)
    service.enable(127)
    assert motor.enabled and service.enabled == {127}
    assert motor.parameters[0x7018] == pytest.approx(0.5)
    assert motor.parameters[0x700A] == 0
    for direction in (1, -1):
        service.jog_press(127, direction)
        assert motor.parameters[0x700A] == pytest.approx(0.2 * direction)
        service.jog_release(127)
        assert motor.parameters[0x700A] == 0
    service.stop(127)
    assert not motor.enabled and not service.enabled


def test_jog_requires_velocity_mode(setup_motor):
    service, _, _ = setup_motor
    service.config.mode = 1
    service.enable(127)
    with pytest.raises(RuntimeError, match="velocity mode"):
        service.jog_press(127, 1)


def test_no_edit_or_scan_while_enabled(setup_motor):
    service, _, _ = setup_motor
    service.enable(127)
    with pytest.raises(RuntimeError):
        service.write(127, "mode", 0)
    with pytest.raises(RuntimeError):
        service.scan([127])


def test_change_id_confirmation_and_official_layout(setup_motor):
    service, fake, motor = setup_motor
    with pytest.raises(ValueError, match="confirmation"):
        service.change_id(127, 128)
    service.change_id(127, 128, True)
    frames = [m for m in fake.sent if m.arbitration_id >> 24 == C.SET_DEVICE_ID]
    assert frames[0].arbitration_id == 0x0780FF7F  # official C++ Set_CAN_ID
    assert frames[0].data == bytes(8)
    assert motor.motor_id == 128 and service.motors == {128: motor.uuid.hex()}
    assert service.read(128, "mode") == 2


def test_id_collision_and_multiple_motors():
    service = MotorService()
    fake = FakeCanTransport([SimulatedRS02(1), SimulatedRS02(2)])
    service.connect(fake)
    service.scan([1])
    with pytest.raises(ValueError, match="already in use"):
        service.change_id(1, 2, True)
    service.scan([2])
    with pytest.raises(RuntimeError, match="one discovered"):
        service.change_id(1, 3, True)
    service.disconnect()


@pytest.mark.parametrize("cleanup", ["disconnect", "application_exit"])
def test_cleanup_order(setup_motor, cleanup):
    service, fake, motor = setup_motor
    service.enable(127)
    service.jog_press(127, 1)
    fake.sent.clear()
    assert getattr(service, cleanup)() == []
    assert [m.arbitration_id >> 24 for m in fake.sent] == [C.WRITE_PARAMETER, C.DISABLE]
    assert fake.sent[0].data == struct.pack("<HHf", 0x700A, 0, 0.0)
    assert fake.closed and not motor.enabled
    assert service.sdk is None
    assert service.disconnect() == []


def test_cleanup_continues_after_zero_failure(setup_motor, monkeypatch):
    service, fake, _ = setup_motor
    original = fake.send

    def failing_send(msg):
        if msg.arbitration_id >> 24 == C.WRITE_PARAMETER:
            raise OSError("USB failed")
        original(msg)

    monkeypatch.setattr(fake, "send", failing_send)
    errors = service.disconnect()
    assert errors and "zero velocity" in errors[0]
    assert fake.closed
    assert fake.sent[-1].arbitration_id >> 24 == C.DISABLE


def test_cleanup_continues_after_disable_failure(setup_motor, monkeypatch):
    service, fake, _ = setup_motor
    original = fake.send

    def failing_send(msg):
        if msg.arbitration_id >> 24 == C.DISABLE:
            raise OSError("USB failed")
        original(msg)

    monkeypatch.setattr(fake, "send", failing_send)
    assert "disable" in service.disconnect()[0]
    assert fake.closed


def test_rx_correlation_ignores_stale_and_wrong_motor(setup_motor):
    service, fake, _ = setup_motor
    fake.reply(C.READ_PARAMETER, 126, 255, struct.pack("<HHf", 0x7018, 0, 99))
    fake.reply(C.READ_PARAMETER, 127, 255, struct.pack("<HHf", 0x7017, 0, 99))
    fake.reply(C.READ_PARAMETER, 127, 254, struct.pack("<HHf", 0x7018, 0, 99))
    assert service.read(127, "current_limit") == pytest.approx(0.5)


def test_timeout_is_bounded():
    fake = FakeCanTransport([])
    sdk = SdkAdapter(fake)
    sdk.register(127)
    with pytest.raises(TimeoutError):
        sdk.read("127", PARAMETERS["mode"])


def test_fault_does_not_pass_as_success(setup_motor):
    service, _, motor = setup_motor
    motor.faults = 2
    with pytest.raises(RuntimeError, match="fault"):
        service.enable(127)
    service.disconnect()
    assert not motor.enabled


def test_csv_and_json(setup_motor, tmp_path):
    service, _, _ = setup_motor
    file = tmp_path / "config.json"
    service.config.save(file)
    assert Configuration.load(file) == service.config
    raw = json.loads(file.read_text())
    assert "enabled" not in raw
    raw["current_limit"] = 100
    file.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        Configuration.load(file)
    log = tmp_path / "log.csv"
    service.log.export(log)
    rows = list(csv.DictReader(log.open()))
    assert any(r["direction"] == "TX" for r in rows)
    assert any(r["direction"] == "RX" for r in rows)


def test_telemetry_current_is_not_torque(setup_motor):
    service, _, motor = setup_motor
    motor.parameters[0x701A] = 0.12
    telemetry = service.telemetry(127)
    assert telemetry["current"] == pytest.approx(0.12)
    assert telemetry["temperature"] == 25


@pytest.mark.parametrize("mode", [0, 1, 3])
def test_mode_neutral_targets_before_enable(setup_motor, mode):
    service, fake, motor = setup_motor
    service.config.mode = mode
    motor.parameters[0x7006] = 10.0
    motor.parameters[0x7016] = 10.0
    service.enable(127)
    enable_index = next(i for i, msg in enumerate(fake.sent) if msg.arbitration_id >> 24 == 3)
    if mode == 3:
        assert motor.parameters[0x7006] == 0
    elif mode == 1:
        assert motor.parameters[0x7016] == motor.parameters[0x7019]
    else:
        operation_index = next(
            i for i, msg in enumerate(fake.sent) if msg.arbitration_id >> 24 == 1
        )
        assert operation_index < enable_index
        assert fake.sent[operation_index].data[4:] == bytes(4)  # zero kp/kd


@pytest.mark.parametrize(
    "key,value", [("mode", True), ("channel", 0.5), ("current_limit", True), ("bitrate", "1000000")]
)
def test_json_types_rejected(key, value, tmp_path):
    from dataclasses import asdict

    data = asdict(Configuration())
    data[key] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Configuration.load(path)


def test_sdk_wire_fixtures(setup_motor):
    service, fake, _ = setup_motor
    fake.sent.clear()
    service.read(127, "mode")
    assert fake.sent[0].arbitration_id == 0x1100FF7F
    assert fake.sent[0].data == bytes.fromhex("0570000000000000")
    fake.sent.clear()
    service.write(127, "current_limit", 0.5)
    assert fake.sent[0].arbitration_id == 0x1200FF7F
    assert fake.sent[0].data == bytes.fromhex("187000000000003f")
    fake.sent.clear()
    service.enable(127)
    assert fake.sent[-1].arbitration_id == 0x0300FF7F
    assert fake.sent[-1].data == bytes(8)
    service.stop(127)
    assert fake.sent[-1].arbitration_id == 0x0400FF7F


def test_unconfirmed_id_change_keeps_both_cleanup_targets(setup_motor, monkeypatch):
    service, fake, motor = setup_motor
    original = fake.send

    def drop_new_uuid(msg):
        if msg.arbitration_id >> 24 == 0 and msg.arbitration_id & 255 == 128:
            fake.sent.append(msg)
        else:
            original(msg)

    monkeypatch.setattr(fake, "send", drop_new_uuid)
    with pytest.raises(TimeoutError):
        service.change_id(127, 128, True)
    assert service.safety_targets == {127, 128}
    assert motor.motor_id == 128
    service.disconnect()
    assert fake.closed and not motor.enabled


def test_discovery_disables_already_running_motor():
    motor = SimulatedRS02(enabled=True)
    motor.parameters[0x700A] = 1.0
    service = MotorService()
    fake = FakeCanTransport([motor])
    service.connect(fake)
    service.scan([127])
    assert not motor.enabled and motor.parameters[0x700A] == 0
    service.disconnect()


def test_vendor_sources_remain_byte_identical():
    import hashlib
    from pathlib import Path

    expected = {
        "__init__.py": "e799265b8f5b8a8d163c6842fbc218bbf12a063041c2a50b914a1fb2a951794e",
        "bus.py": "90b069ea92191253123ae9189594ee57bd56bc5ce03f5de95892208a86b8b1d2",
        "protocol.py": "08dcca9c58d611a43d20e74078b047db60b26b2807d6a9e2397d23e73936f800",
        "table.py": "d245eefd51f2ce7df5929c1bc22c8c14455dd95b2ccb78821fc17e3925b97a4d",
    }
    root = Path(__file__).resolve().parents[1] / "vendor" / "robstride_dynamics"
    for name, checksum in expected.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == checksum
