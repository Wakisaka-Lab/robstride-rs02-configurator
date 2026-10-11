"""Two-channel extension of python-can's gs_usb backend.

Control wValue and frame.channel follow INNO-MAKER/USB2CAN-X2's gs_usb.c.
Uses libusb-package for a verified architecture-matched Windows libusb DLL.
Only one channel is opened by this application at a time.
"""

import platform
import struct

import can
import libusb_package
import usb.core
import usb.util
from can.interfaces.gs_usb import GsUsbBus
from gs_usb.constants import (
    GS_CAN_MODE_HW_TIMESTAMP,
    GS_CAN_MODE_LISTEN_ONLY,
    GS_CAN_MODE_LOOP_BACK,
    GS_CAN_MODE_ONE_SHOT,
)
from gs_usb.gs_usb import GsUsb
from gs_usb.gs_usb_frame import GsUsbFrame
from usb.backend import libusb1


def usb_backend():
    backend = libusb1.get_backend(find_library=libusb_package.find_library)
    if backend is None:
        raise RuntimeError(
            "libusb could not initialize: library, driver or USB access unavailable. "
            "On Windows verify libusb-package and Zadig libusbK. Simulation is available."
        )
    return backend


def discover() -> list[dict]:
    devices = usb.core.find(
        find_all=True, custom_match=GsUsb.is_gs_usb_device, backend=usb_backend()
    )
    result = []
    for dev in devices:
        try:
            result.append(
                {
                    "bus": dev.bus,
                    "address": dev.address,
                    "vid": dev.idVendor,
                    "pid": dev.idProduct,
                    "product": dev.product or "gs_usb device",
                    "serial": dev.serial_number or "unknown",
                }
            )
        finally:
            usb.util.dispose_resources(dev)
    return result


class ChannelDevice:
    def __init__(self, device, channel: int):
        self.device = device
        self.channel = channel

    def __getattr__(self, name):
        return getattr(self.device, name)

    def ctrl_transfer(self, request_type, request, value, index, data):
        # gs_usb channel is wValue, NOT USB interface number (wIndex).
        if request in (1, 2, 4):
            value = self.channel
        return self.device.ctrl_transfer(request_type, request, value, index, data, timeout=500)


class ChannelGsUsb(GsUsb):
    def __init__(self, device, channel: int):
        super().__init__(ChannelDevice(device, channel))
        self.channel = channel

    def start(self, flags=GS_CAN_MODE_HW_TIMESTAMP):
        # Channel reset/start follows the manufacturer's kernel driver. A full USB reset
        # would disrupt the other channel and may invalidate bitrate/host-format setup.
        if platform.system() != "Windows" and self.gs_usb.is_kernel_driver_active(0):
            self.gs_usb.detach_kernel_driver(0)
        flags &= self.device_capability.feature
        flags &= (
            GS_CAN_MODE_HW_TIMESTAMP
            | GS_CAN_MODE_LISTEN_ONLY
            | GS_CAN_MODE_LOOP_BACK
            | GS_CAN_MODE_ONE_SHOT
        )
        self.device_flags = flags
        self.gs_usb.ctrl_transfer(0x41, 2, 0, 0, struct.pack("<II", 1, flags))

    def send(self, frame):
        frame.channel = self.channel
        return super().send(frame)

    def read(self, frame, timeout_ms):
        timestamp = bool(self.device_flags & GS_CAN_MODE_HW_TIMESTAMP)
        try:
            data = self.gs_usb.read(0x81, frame.__sizeof__(timestamp), timeout_ms)
        except usb.core.USBTimeoutError:
            return False
        GsUsbFrame.unpack_into(frame, data, timestamp)
        return frame.channel == self.channel


class MultiChannelGsUsbBus(GsUsbBus):
    def __init__(self, device: dict, channel: int, bitrate: int):
        if channel not in (0, 1):
            raise ValueError("CAN channel must be 0 or 1")
        raw = usb.core.find(
            custom_match=GsUsb.is_gs_usb_device,
            bus=device["bus"],
            address=device["address"],
            backend=usb_backend(),
        )
        if raw is None:
            raise RuntimeError("Selected USB device disappeared; enumerate again")
        self._is_shutdown = False
        self._index = None
        self._bitrate = bitrate
        self.gs_usb = ChannelGsUsb(raw, channel)
        self.channel_info = f"USB {device['bus']}:{device['address']} CAN{channel}"
        self._can_protocol = can.CanProtocol.CAN_20
        try:
            # Official gs_usb host byte-order negotiation precedes device queries.
            raw.ctrl_transfer(0x41, 0, 1, 0, struct.pack("<I", 0x0000BEEF), timeout=500)
            if channel > self.gs_usb.device_info.icount:
                raise ValueError("Selected CAN channel not reported by this adapter")
            self.gs_usb.stop()
            timing = can.BitTiming.from_sample_point(
                f_clock=self.gs_usb.device_capability.fclk_can, bitrate=bitrate, sample_point=87.5
            )
            self.gs_usb.set_timing(1, timing.tseg1 - 1, timing.tseg2, timing.sjw, timing.brp)
            self.gs_usb.start()
            can.BusABC.__init__(self, channel=self.channel_info)
        except Exception:
            self.gs_usb.stop()
            usb.util.dispose_resources(raw)
            self._is_shutdown = True
            raise

    def shutdown(self):
        if self._is_shutdown:
            return
        try:
            super().shutdown()
        finally:
            usb.util.dispose_resources(self.gs_usb.gs_usb.device)
