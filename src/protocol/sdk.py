"""Transport adapter around the pinned official SDK, with bounded correlated RX.

ID-change correction follows RobStride/SampleProgram RS/Robstride01.cpp Set_CAN_ID.
The vendored SDK remains unchanged. See vendor/PROVENANCE.md.
"""

import struct
import time
from dataclasses import dataclass

from robstride_dynamics.bus import Motor, RobstrideBus
from robstride_dynamics.protocol import CommunicationType as C
from robstride_dynamics.protocol import ParameterType as P
from robstride_dynamics.table import (
    MODEL_MIT_POSITION_TABLE,
    MODEL_MIT_TORQUE_TABLE,
    MODEL_MIT_VELOCITY_TABLE,
)

from transport.can_transport import CanTransport

PARAMETERS = {
    "mode": P.MODE,
    "current_limit": P.CURRENT_LIMIT,
    "velocity_limit": P.VELOCITY_LIMIT,
    "acceleration": P.VEL_ACCELERATION_TARGET,
    "velocity_target": P.VELOCITY_TARGET,
    "position": P.MECHANICAL_POSITION,
    "velocity": P.MECHANICAL_VELOCITY,
    "current": P.IQ_FILTERED,
    "bus_voltage": P.VBUS,
}
WRITABLE = {"mode", "current_limit", "velocity_limit", "acceleration"}


@dataclass
class Status:
    state: int = 0
    faults: int = 0
    position: float = 0
    velocity: float = 0
    torque: float = 0
    temperature: float = 0


class SdkAdapter(RobstrideBus):
    def __init__(self, transport: CanTransport):
        super().__init__("abstract", {})
        self.channel_handler = transport
        self.status: dict[int, Status] = {}
        self.pending_index: int | None = None
        self.target = 0

    def __del__(self):
        # Service owns explicit safe shutdown, never perform I/O from a destructor.
        pass

    def register(self, motor_id: int) -> str:
        name = str(motor_id)
        self.motors[name] = Motor(motor_id, "rs-02")
        return name

    def transmit(self, communication_type, extra_data, device_id, data=bytes(8)):
        self.target = device_id
        self.pending_index = (
            int.from_bytes(data[:2], "little") if communication_type == C.READ_PARAMETER else None
        )
        super().transmit(communication_type, extra_data, device_id, data)

    def correlated(self, kind: int, timeout: float = 0.15, target: int | None = None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.channel_handler.recv(max(0, deadline - time.monotonic()))
            if frame is None:
                break
            if (
                not frame.is_rx
                or not frame.is_extended_id
                or frame.is_error_frame
                or frame.is_remote_frame
                or frame.is_fd
                or len(frame.data) != 8
            ):
                continue
            identifier = frame.arbitration_id
            typ, extra, host = identifier >> 24, (identifier >> 8) & 0xFFFF, identifier & 255
            source = extra & 255
            if source != (self.target if target is None else target):
                continue
            # Official C++ identifies UUID replies by destination 0xFE.
            if host not in (self.host_id, 0xFE if typ == C.GET_DEVICE_ID else self.host_id):
                continue
            if typ == C.OPERATION_STATUS:
                p, v, torque, temp = struct.unpack(">HHHH", frame.data)
                self.status[source] = Status(
                    extra >> 14,
                    (extra >> 8) & 63,
                    (p / 32767 - 1) * float(MODEL_MIT_POSITION_TABLE["rs-02"]),
                    (v / 32767 - 1) * MODEL_MIT_VELOCITY_TABLE["rs-02"],
                    (torque / 32767 - 1) * MODEL_MIT_TORQUE_TABLE["rs-02"],
                    temp / 10,
                )
                if self.status[source].faults:
                    raise RuntimeError(
                        f"Motor {source} status faults: {self.status[source].faults:#x}"
                    )
            if typ == C.FAULT_REPORT:
                faults, warnings = struct.unpack("<II", frame.data)
                self.status[source] = Status(faults=faults | warnings)
                raise RuntimeError(f"Motor {source} fault {faults:#x}, warning {warnings:#x}")
            if typ != kind:
                continue
            if (
                kind == C.READ_PARAMETER
                and int.from_bytes(frame.data[:2], "little") != self.pending_index
            ):
                continue
            return extra, bytes(frame.data)
        raise TimeoutError(f"No correlated type {kind} reply from ID {self.target}")

    def ping_by_id(self, device_id, timeout=0.03):
        self.transmit(C.GET_DEVICE_ID, self.host_id, device_id)
        try:
            extra, uuid = self.correlated(C.GET_DEVICE_ID, timeout)
            return extra & 255, uuid
        except TimeoutError:
            return None

    def receive_read_frame(self) -> bytes:
        return self.correlated(C.READ_PARAMETER)[1][4:]

    def receive_status_frame(self, motor):
        self.correlated(C.OPERATION_STATUS)
        s = self.status[int(motor)]
        return s.position, s.velocity, s.torque, s.temperature

    def write_id(self, motor, new_id):
        old_id = self.motors[motor].id
        self.transmit(C.SET_DEVICE_ID, (new_id << 8) | self.host_id, old_id)
        # Verify new ID with an explicit UUID query; ID-change acknowledgement varies.
        reply = self.ping_by_id(new_id, timeout=0.15)
        if reply is None:
            raise TimeoutError("ID change uncertain; rescan before sending further commands")
        self.motors[motor].id = new_id
        return reply
