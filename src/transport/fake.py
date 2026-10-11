"""Deterministic RS02 simulation using the official protocol's message definitions.

Not evidence of firmware or hardware compatibility.
"""

import struct
from collections import deque
from dataclasses import dataclass, field

import can
from robstride_dynamics.protocol import CommunicationType as C


@dataclass
class SimulatedRS02:
    motor_id: int = 127
    uuid: bytes = b"SIMRS002"
    enabled: bool = False
    faults: int = 0
    parameters: dict[int, float | int] = field(
        default_factory=lambda: {
            0x7005: 2,
            0x700A: 0.0,
            0x7018: 0.5,
            0x7017: 0.5,
            0x7022: 0.5,
            0x7019: 0.0,
            0x701B: 0.0,
            0x701A: 0.0,
            0x701C: 24.0,
        }
    )


class FakeCanTransport:
    def __init__(self, motors: list[SimulatedRS02] | None = None):
        self.motors = motors if motors is not None else [SimulatedRS02()]
        self.rx: deque[can.Message] = deque()
        self.sent: list[can.Message] = []
        self.closed = False

    def send(self, msg: can.Message) -> None:
        if self.closed:
            raise RuntimeError("Transport closed")
        self.sent.append(msg)
        typ, extra, target = (
            msg.arbitration_id >> 24,
            (msg.arbitration_id >> 8) & 65535,
            msg.arbitration_id & 255,
        )
        for m in self.motors:
            if target != m.motor_id:
                continue
            host = extra & 255
            if typ == C.SET_DEVICE_ID:
                m.motor_id = extra >> 8
                return
            if typ == C.GET_DEVICE_ID:
                self.reply(0, m.motor_id, 0xFE, m.uuid)
            elif typ == C.READ_PARAMETER:
                index = int.from_bytes(msg.data[:2], "little")
                value = m.parameters[index]
                payload = (
                    struct.pack("<bxxx", int(value))
                    if index == 0x7005
                    else struct.pack("<f", value)
                )
                self.reply(typ, m.motor_id, host, bytes(msg.data[:4]) + payload)
            else:
                if typ == C.WRITE_PARAMETER:
                    index = int.from_bytes(msg.data[:2], "little")
                    m.parameters[index] = (
                        msg.data[4] if index == 0x7005 else struct.unpack("<f", msg.data[4:])[0]
                    )
                    if index == 0x700A:
                        m.parameters[0x701B] = m.parameters[index] if m.enabled else 0.0
                elif typ == C.OPERATION_CONTROL:
                    # The app only sends neutral MIT commands (zero gains/torque/speed).
                    pass
                elif typ == C.ENABLE:
                    m.enabled = True
                elif typ == C.DISABLE:
                    m.enabled = False
                    m.parameters[0x701B] = 0.0
                else:
                    raise ValueError("Unsupported simulated command")
                self.reply(
                    C.OPERATION_STATUS,
                    m.motor_id | (m.faults << 8) | ((2 if m.enabled else 0) << 14),
                    host,
                    struct.pack(">HHHH", 32767, 32767, 32767, 250),
                )

    def reply(self, kind: int, extra: int, host: int, data: bytes) -> None:
        self.rx.append(
            can.Message(
                arbitration_id=(kind << 24) | (extra << 8) | host, data=data, is_extended_id=True
            )
        )

    def recv(self, timeout: float | None = None) -> can.Message | None:
        return self.rx.popleft() if self.rx else None

    def shutdown(self) -> None:
        self.closed = True
