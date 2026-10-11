"""Classical extended CAN transport boundaries; no protocol logic here."""

from typing import Protocol

import can


class CanTransport(Protocol):
    def send(self, msg: can.Message) -> None: ...
    def recv(self, timeout: float | None = None) -> can.Message | None: ...
    def shutdown(self) -> None: ...


class PythonCanTransport:
    def __init__(self, **options):
        self.bus = can.Bus(**options)

    def send(self, msg: can.Message) -> None:
        if not msg.is_extended_id or msg.is_fd or len(msg.data) > 8:
            raise ValueError("Only classical extended CAN is supported")
        self.bus.send(msg, timeout=0.1)

    def recv(self, timeout: float | None = None) -> can.Message | None:
        return self.bus.recv(timeout)

    def shutdown(self) -> None:
        self.bus.shutdown()


class GsUsbTransport(PythonCanTransport):
    @staticmethod
    def enumerate_devices() -> list[dict]:
        # Discover using the gs_usb device matcher and bundled libusb backend.
        from transport.gs_channels import discover

        return discover()

    def __init__(self, device: dict, channel: int = 0, bitrate: int = 1_000_000):
        from transport.gs_channels import MultiChannelGsUsbBus

        self.bus = MultiChannelGsUsbBus(device, channel, bitrate)


class SocketCanTransport(PythonCanTransport):
    def __init__(self, channel: str = "can0", bitrate: int = 1_000_000):
        # SocketCAN bitrate must be configured by the OS; python-can cannot set it.
        super().__init__(interface="socketcan", channel=channel, bitrate=bitrate)
