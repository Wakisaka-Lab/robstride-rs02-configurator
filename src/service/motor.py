"""Single-owner service. All hardware operations belong to the CAN worker."""

import logging
import math
from dataclasses import asdict
from typing import Callable, Iterable

from robstride_dynamics.protocol import ParameterType as P

from protocol.sdk import PARAMETERS, WRITABLE, SdkAdapter, Status
from service.config import SETTING_LIMITS, Configuration
from service.logging import CanLog, LoggedTransport
from transport.can_transport import CanTransport

logger = logging.getLogger("rs02.service")


class MotorService:
    def __init__(
        self, config: Configuration | None = None, sink: Callable[[dict], None] | None = None
    ):
        self.config = config or Configuration()
        self.log = CanLog(sink)
        self.sdk: SdkAdapter | None = None
        self.motors: dict[int, str] = {}
        self.enabled: set[int] = set()
        self.safety_targets: set[int] = set()
        self.jogging: int | None = None

    def connect(self, transport: CanTransport) -> None:
        if self.sdk is not None:
            raise RuntimeError("Already connected")
        self.config.validate()
        self.sdk = SdkAdapter(LoggedTransport(transport, self.log.append))
        self.enabled.clear()
        logger.info("connected; no motor has been enabled")

    def require(self, motor_id: int) -> SdkAdapter:
        if self.sdk is None or motor_id not in self.motors:
            raise RuntimeError("Connect and scan/select a discovered motor first")
        return self.sdk

    def scan(
        self, ids: Iterable[int] = range(1, 255), cancel: Callable[[], bool] = lambda: False
    ) -> dict[int, str]:
        if self.sdk is None:
            raise RuntimeError("Not connected")
        if self.enabled:
            raise RuntimeError("Disable before scanning")
        for motor_id in ids:
            if cancel():
                break
            if not 1 <= motor_id <= 254:
                raise ValueError("Motor ID must be 1..254; host ID 255 is reserved")
            reply = self.sdk.ping_by_id(motor_id)
            if reply:
                found_id, uuid = reply
                self.sdk.register(found_id)
                self.motors[found_id] = uuid.hex()
                self.safety_targets.add(found_id)
                # A motor may already be running when connected. Quiesce every discovered motor.
                self.stop(found_id)
        return dict(self.motors)

    def read(self, motor_id: int, parameter: str) -> float | int:
        sdk = self.require(motor_id)
        return sdk.read(str(motor_id), PARAMETERS[parameter])

    def write(self, motor_id: int, parameter: str, value: float | int) -> None:
        sdk = self.require(motor_id)
        if motor_id in self.enabled:
            raise RuntimeError("Disable before editing parameters")
        if parameter not in WRITABLE:
            raise ValueError("Read-only or unsupported parameter")
        if not math.isfinite(value):
            raise ValueError("Finite value required")
        if parameter == "mode":
            if value not in (0, 1, 2, 3):
                raise ValueError("Mode must be 0, 1, 2 or 3")
            value = int(value)
        elif not 0 < value <= SETTING_LIMITS[parameter]:
            raise ValueError(f"{parameter} must be >0 and <= {SETTING_LIMITS[parameter]}")
        sdk.write(str(motor_id), PARAMETERS[parameter], value)
        actual = self.read(motor_id, parameter)
        if not math.isclose(actual, value, rel_tol=1e-5, abs_tol=1e-6):
            raise RuntimeError(f"Readback mismatch: {actual} != {value}")

    def configure(self, motor_id: int) -> None:
        self.config.validate()
        for key in ("mode", "current_limit", "velocity_limit", "acceleration"):
            self.write(motor_id, key, getattr(self.config, key))
        sdk = self.require(motor_id)
        sdk.write(str(motor_id), PARAMETERS["velocity_target"], 0.0)
        # Clear retained targets for the chosen mode before explicit enable.
        if self.config.mode == 3:
            sdk.write(str(motor_id), P.IQ_TARGET, 0.0)
        elif self.config.mode == 1:
            position = self.read(motor_id, "position")
            sdk.write(str(motor_id), P.POSITION_TARGET, position)
        elif self.config.mode == 0:
            sdk.write_operation_frame(
                str(motor_id), position=0.0, kp=0.0, kd=0.0, velocity=0.0, torque=0.0
            )
            sdk.receive_status_frame(str(motor_id))

    def enable(self, motor_id: int, permitted: Callable[[], bool] = lambda: True) -> None:
        sdk = self.require(motor_id)
        self.configure(motor_id)
        if not permitted():
            return
        # Track before sending; an acknowledgement failure may still leave hardware enabled.
        self.enabled.add(motor_id)
        sdk.enable(str(motor_id))
        if sdk.status[motor_id].state != 2:
            raise RuntimeError("Motor did not report running state")

    def jog_press(
        self, motor_id: int, direction: int, still_held: Callable[[], bool] = lambda: True
    ) -> None:
        sdk = self.require(motor_id)
        self.config.validate()
        if motor_id not in self.enabled or direction not in (-1, 1):
            raise RuntimeError("Explicit enable and valid jog direction required")
        if self.read(motor_id, "mode") != 2:
            raise RuntimeError("Jog requires velocity mode (2)")
        limit = self.read(motor_id, "velocity_limit")
        if self.config.jog_velocity > limit:
            raise ValueError("Jog exceeds motor velocity limit")
        # Release/stop may arrive while the prerequisite reads are in flight.
        if not still_held():
            return
        self.jogging = motor_id
        sdk.write(
            str(motor_id), PARAMETERS["velocity_target"], direction * self.config.jog_velocity
        )

    def jog_release(self, motor_id: int) -> None:
        sdk = self.require(motor_id)
        sdk.write(str(motor_id), PARAMETERS["velocity_target"], 0.0)
        self.jogging = None

    def stop(self, motor_id: int) -> None:
        sdk = self.require(motor_id)
        try:
            sdk.write(str(motor_id), PARAMETERS["velocity_target"], 0.0)
        finally:
            try:
                sdk.disable(str(motor_id))
                if sdk.status[motor_id].state == 2:
                    raise RuntimeError("Motor still reports running after disable")
            finally:
                self.enabled.discard(motor_id)
                self.jogging = None

    def change_id(self, motor_id: int, new_id: int, confirmed: bool = False) -> None:
        sdk = self.require(motor_id)
        if not confirmed:
            raise ValueError("Explicit confirmation required; connect only ONE motor")
        if len(self.motors) != 1 or motor_id in self.enabled:
            raise RuntimeError("Only one discovered, disabled motor is allowed")
        if not 1 <= new_id <= 254 or new_id == motor_id:
            raise ValueError("Choose a different ID in 1..254")
        if sdk.ping_by_id(new_id):
            raise ValueError("New ID already in use")
        self.stop(motor_id)
        # Retain both addresses during uncertain ID change for best-effort cleanup.
        self.safety_targets.add(new_id)
        sdk.register(new_id)
        reply = sdk.write_id(str(motor_id), new_id)
        if reply[1].hex() != self.motors[motor_id]:
            raise RuntimeError("UUID mismatch after ID change")
        self.motors[new_id] = self.motors.pop(motor_id)
        self.safety_targets.discard(motor_id)
        sdk.motors.pop(str(motor_id))
        sdk.register(new_id)

    def telemetry(self, motor_id: int) -> dict:
        sdk = self.require(motor_id)
        values = asdict(sdk.status.get(motor_id, Status()))
        # Current is read as iqf; torque feedback must not be mislabeled as current.
        values.update(
            {key: self.read(motor_id, key) for key in ("position", "velocity", "current")}
        )
        return {
            "id": motor_id,
            "uuid": self.motors[motor_id],
            "enabled": motor_id in self.enabled,
            **values,
        }

    def disconnect(self) -> list[str]:
        sdk = self.sdk
        if sdk is None:
            return []
        errors = []
        for motor_id in sorted(self.safety_targets):
            sdk.register(motor_id)
            try:
                sdk.write(str(motor_id), PARAMETERS["velocity_target"], 0.0)
            except Exception as e:
                errors.append(f"ID {motor_id} zero velocity: {e}")
            try:
                sdk.disable(str(motor_id))
                if sdk.status[motor_id].state == 2:
                    raise RuntimeError("Motor still reports running after disable")
            except Exception as e:
                errors.append(f"ID {motor_id} disable: {e}")
        try:
            sdk.channel_handler.shutdown()
        except Exception as e:
            errors.append(f"Transport close: {e}")
        finally:
            self.sdk = None
            self.enabled.clear()
            self.motors.clear()
            self.safety_targets.clear()
            self.jogging = None
        for error in errors:
            logger.error(error)
        logger.info("disconnected")
        return errors

    def application_exit(self) -> list[str]:
        return self.disconnect()
