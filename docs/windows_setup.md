# Windows 10/11 x64: USB2CAN-X2 setup

Use Python 3.11 x64 and the locked dependencies from README.md. Manufacturer reference:
https://github.com/INNO-MAKER/USB2CAN-X2 (`For Windows/Python` and USB2CAN-X2 UserManual).
This repository uses **python-can gs_usb**, not the manufacturer's C# DLL API.
No physical X2 or RS02 was available during development.

1. Disconnect motor power. Connect only the X2 to USB. Record its product name, serial, VID/PID,
   current driver and physical CAN0/CAN1 label. Close vendor CAN software and other CAN clients.
2. Obtain Zadig from https://zadig.akeo.ie/ and run it as administrator. Select
   **Options → List All Devices**. Identify the X2 precisely; do not select another USB device,
   keyboard, hub or system device. Firmware/Windows may expose one USB interface for both CAN channels;
   the application routes CAN0/CAN1 in gs_usb packets, not by assuming separate USB interfaces.
3. Install/replace the driver for the confirmed X2 interface with **libusbK**.
   PyUSB/libusb1 can use libusbK; WinUSB is another supported libusb binding if libusbK is unavailable.
   Do not choose **libusb-win32** (the manufacturer warns it can cause incorrect recognition).
   Record the original binding for restoration through Device Manager or vendor driver installation.
   Replacing a driver may stop other vendor applications from working; do not share the adapter.
4. Install the project. `libusb-package` provides libusb-1.0.dll with matching architecture;
   do not download random DLLs, disable verification, or overwrite Windows System32 DLLs.
5. With motor power still off, run:

   ```powershell
   uv run --frozen python hardware_smoke_test.py --list
   ```

   This enumerates gs_usb-compatible devices without sending motor frames. Expect a compatible
   device entry; no-device errors mean the driver, firmware VID/PID, USB connection or libusb binding
   needs diagnosis. The pinned gs-usb supports VID/PID pairs `1d50:606f`, `1209:2323`,
   `1cd2:606f`, `16d0:10b8`; do not silently assume every X2 firmware revision uses these IDs.
6. Use the X2 manual's pinout for CAN_H/CAN_L and reference/ground. Provide manufacturer-approved
   RS02 power. Install termination at both ends of the short CAN bus (typically 120 Ω each,
   approximately 60 Ω across H/L while unpowered); account for built-in termination switches.
   Secure the motor and keep the shaft unloaded. Verify polarity and have a physical power cutoff.
7. Power one RS02 configured for its **private RobStride protocol**, classical extended CAN,
   **1 Mbit/s**. This app does not guess or change a motor's protocol/bitrate. Connect CAN0 first.
8. Run the read-only diagnostic (replace the ID with the known motor ID):

   ```powershell
   uv run --frozen python hardware_smoke_test.py --backend gs_usb --device 0 --channel 0 --motor-id 127 --csv rs02-can0.csv
   ```

   It never enables or jogs. Discovery and final cleanup do send zero-velocity writes and disable
   requests, so this is not a passive sniffer. Confirm UUID, parameters, plausible readings and
   successful cleanup. No response is a failed hardware check, not a successful connection.
9. Repeat with `--channel 1` after moving the wires to labelled CAN1. Do not assume the channel
   numbering until verified. Test the GUI, then motion in the sequence in STATUS.md.

If permission/busy errors occur, unplug/replug and close competing clients. For USB backend errors,
verify Python and DLL architecture and Device Manager binding. For no motor reply, check physical
channel, wiring, termination, power, ID, protocol and bitrate before attempting motion. Do not run
loopback examples with a powered motor, random test frames, or another CAN controller's protocol.
