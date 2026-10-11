from unittest.mock import MagicMock

import can
import pytest
from gs_usb.gs_usb_frame import GsUsbFrame

from transport.can_transport import GsUsbTransport, PythonCanTransport, SocketCanTransport
from transport.gs_channels import ChannelDevice, ChannelGsUsb, MultiChannelGsUsbBus


def test_channel_control_wvalue_and_tx_frame():
    raw = MagicMock()
    proxy = ChannelDevice(raw, 1)
    proxy.ctrl_transfer(0x41, 2, 0, 0, b"payload")
    raw.ctrl_transfer.assert_called_with(0x41, 2, 1, 0, b"payload", timeout=500)
    device = ChannelGsUsb(raw, 1)
    device.device_flags = 0
    frame = GsUsbFrame(0x80000000 | 0x1100FF7F, bytes(8))
    device.send(frame)
    assert frame.channel == 1
    packet = raw.write.call_args.args[1]
    assert packet[9] == 1


def test_rx_ignores_other_channel_and_usb_timeout():
    raw = MagicMock()
    device = ChannelGsUsb(raw, 1)
    device.device_flags = 0
    wrong = GsUsbFrame(0x80000000, bytes(8))
    raw.read.return_value = wrong.pack(False)
    assert not device.read(GsUsbFrame(), 10)
    wrong.channel = 1
    raw.read.return_value = wrong.pack(False)
    assert device.read(GsUsbFrame(), 10)


@pytest.mark.parametrize("channel", [0, 1])
def test_gs_backend_constructor_and_channel(monkeypatch, channel):
    import transport.gs_channels as module

    raw = MagicMock()
    raw.ctrl_transfer.side_effect = lambda typ, req, *a, **k: (
        __import__("struct").pack("<4B2I", 0, 0, 0, 1, 1, 1)
        if req == 5
        else __import__("struct").pack("<10I", 0, 48000000, 1, 16, 1, 8, 4, 1, 1024, 1)
        if req == 4
        else b""
    )
    monkeypatch.setattr(module, "usb_backend", lambda: object())
    monkeypatch.setattr(module.usb.core, "find", lambda **kw: raw)
    monkeypatch.setattr(module.usb.util, "dispose_resources", lambda d: None)
    transport = GsUsbTransport({"bus": 1, "address": 2}, channel=channel)
    assert isinstance(transport.bus, MultiChannelGsUsbBus)
    assert transport.bus.gs_usb.channel == channel
    raw.reset.assert_not_called()
    transport.shutdown()
    assert transport.bus._is_shutdown


def test_socketcan_constructor(monkeypatch):
    import transport.can_transport as module

    factory = MagicMock()
    monkeypatch.setattr(module.can, "Bus", factory)
    SocketCanTransport("can1")
    factory.assert_called_with(interface="socketcan", channel="can1", bitrate=1000000)


def test_classical_extended_only(monkeypatch):
    import transport.can_transport as module

    monkeypatch.setattr(module.can, "Bus", MagicMock())
    t = PythonCanTransport(interface="socketcan", channel="can0")
    with pytest.raises(ValueError):
        t.send(can.Message(arbitration_id=1, is_extended_id=False))
    with pytest.raises(ValueError):
        t.send(can.Message(arbitration_id=1, is_extended_id=True, is_fd=True))


def test_enumeration_metadata_and_resource_release(monkeypatch):
    import transport.gs_channels as module

    raw = MagicMock()
    raw.bus, raw.address = 3, 4
    raw.idVendor, raw.idProduct = 0x1D50, 0x606F
    raw.product, raw.serial_number = "USB2CAN-X2", "test-serial"
    monkeypatch.setattr(module, "usb_backend", lambda: object())
    monkeypatch.setattr(module.usb.core, "find", lambda **kw: [raw])
    dispose = MagicMock()
    monkeypatch.setattr(module.usb.util, "dispose_resources", dispose)
    found = GsUsbTransport.enumerate_devices()
    assert found == [
        {
            "bus": 3,
            "address": 4,
            "vid": 0x1D50,
            "pid": 0x606F,
            "product": "USB2CAN-X2",
            "serial": "test-serial",
        }
    ]
    dispose.assert_called_once_with(raw)


def test_usb_access_error_is_not_swallowed():
    import usb.core

    raw = MagicMock()
    raw.read.side_effect = usb.core.USBError("Device disconnected")
    device = ChannelGsUsb(raw, 0)
    device.device_flags = 0
    with pytest.raises(usb.core.USBError):
        device.read(GsUsbFrame(), 10)
