# Official protocol provenance

Unmodified MIT-licensed Python SDK from
https://github.com/ROBSTRIDE-DYNAMICS/Robstride-Dynamics-Python-SDK
at commit `2abbcd6e4b05e4e9d701ff2a9137af56a70c6d11`.
`LICENSE.robstride` is the original license. The package is installed locally alongside this app;
no similarly named third-party SDK is substituted.

Original file SHA-256:

| File | SHA-256 |
| --- | --- |
| `__init__.py` | `e799265b8f5b8a8d163c6842fbc218bbf12a063041c2a50b914a1fb2a951794e` |
| `bus.py` | `90b069ea92191253123ae9189594ee57bd56bc5ce03f5de95892208a86b8b1d2` |
| `protocol.py` | `08dcca9c58d611a43d20e74078b047db60b26b2807d6a9e2397d23e73936f800` |
| `table.py` | `d245eefd51f2ce7df5929c1bc22c8c14455dd95b2ccb78821fc17e3925b97a4d` |

`protocol.sdk.SdkAdapter` subclasses `RobstrideBus`, injecting a transport in place of SocketCAN.
It inherits parameter packing, TX identifier construction, enable, disable and operation-control
encoding. Receive overrides impose deadlines and correlate message type, motor ID, destination,
and parameter index. This fixes the upstream unbounded parameter receive and unrelated-frame
acceptance. RS02 feedback ranges use upstream `table.py`; current comes from `IQ_FILTERED`,
not the torque status field. The simulation emulates these definitions; it is not firmware validation.

Upstream `write_id` references nonexistent `SET_CAN_ID` and places the new ID incorrectly.
Our override uses `SET_DEVICE_ID` and `extra=(new_id << 8) | host_id`, based on
https://github.com/RobStride/SampleProgram/blob/5f598686b05fcc527ee0fc0ea954f0afd652b234/RS/Robstride01.cpp
`Set_CAN_ID`. A UUID query at the new address verifies the outcome; both addresses remain safety
cleanup targets if the change is uncertain. Manufacturer C++ identifies UUID responses with
low identifier byte `0xFE`; the adapter accepts that or the host address only for UUID replies.

USB channel routing is based on INNO-MAKER's published Linux gs_usb driver at
https://github.com/INNO-MAKER/USB2CAN-X2/tree/ac765638b75f202b3d30191077a6f8f24afcced0
under `Document/Advanced Document/If Can not find the Driver On your Linux/gs_usb.c`.
The control channel is `wValue` (not `wIndex`), device channel count is `icount + 1`, and the USB
CAN frame has a channel byte. Python-can 4.6.1 / gs-usb 0.3.0 otherwise hardcode channel 0.
Our `MultiChannelGsUsbBus` subclasses python-can's `GsUsbBus`, reuses its TX/RX conversions,
and adds per-channel control/frame routing. This is not a separate invented motor protocol.
Startup uses channel reset/start (not a whole-device USB reset). Do not share the X2 with other CAN applications.
Hardware and Windows validation are still required.
