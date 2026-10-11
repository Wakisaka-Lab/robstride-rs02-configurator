# RobStride RS02 Configurator

Python 3.11 / PySide6 desktop MVP for Windows 10/11 x64, InnoMaker USB2CAN-X2 and RS02.
Classical CAN, 29-bit identifiers, default **1,000,000 bit/s**. Linux SocketCAN is optional.
The app starts **disconnected**, in clearly labelled **simulated mode**, and never enables a motor
without an explicit user action. Physical communication and Windows packaging are **unverified**.

## Install and run

From the repository root, with [uv](https://docs.astral.sh/uv/) installed:

```sh
uv sync --frozen --extra dev --python 3.11
uv run --frozen rs02-configurator
```

Alternatively on Windows PowerShell (pinned direct dependencies, use uv for the full locked graph):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\rs02-configurator.exe
```

No token or online service is required at runtime. Dependency downloads require PyPI.
The official SDK is bundled, with its MIT license and exact source hashes in
[vendor/PROVENANCE.md](vendor/PROVENANCE.md).

## Workflow

1. Keep **Simulated RS02** selected for software evaluation. Click Connect, then Scan.
   Simulation exposes motor ID 127 and a visibly simulated UUID.
2. For physical use, complete [the Windows setup guide](docs/windows_setup.md), select physical
   gs_usb mode, Enumerate USB, select the device and CAN 0/1, select 1 Mbit/s, then Connect.
   A compatible VID/PID alone cannot prove the device is an X2; inspect its product/serial/label.
3. Scan discovers UUIDs and immediately requests zero velocity and disable for responding motors.
   Scanning IDs 1–254 may take about 8 seconds without replies. Disconnect cancels the scan.
   ID 255 is reserved for the host. Select the discovered motor to poll telemetry.
4. While disabled, select a mode and edit current limit, velocity limit and acceleration.
   Apply writes and reads back the supported parameters. Raw parameter controls expose the SDK
   subset with read-only measurements; unrestricted motion targets and gain editing are excluded.
5. Default limits: **0.5 A**, **0.5 rad/s**, **0.5 rad/s²**, jog **0.2 rad/s**.
   MVP limits cap current/velocity/acceleration at 2 in their respective units, jog at 0.5 rad/s.
   These are conservative software choices, not a certified safe operating envelope.
6. Enable prompts for confirmation, applies/reads back limits and requests a zero velocity target
   before enabling. Current mode also clears Iq target; position mode uses measured position as its target;
   MIT mode sends the SDK neutral zero-gain frame. Jog requires velocity mode (2) and runs only while the button is held.
   Release requests zero; Stop disables. Loss of focus or missing GUI heartbeat for 350 ms
   requests zero and disable. Do not change modes or parameters while enabled.
7. ID changes require a disabled motor, a different ID in 1–254, a collision probe and an explicit
   warning/confirmation. **Physically connect only ONE motor**. Verification compares its UUID
   at the new address; an uncertain change closes communication and attempts cleanup at both IDs.
8. Save/load JSON stores desired settings only, never connection or enabled state. Loading requires
   disconnection and does not connect or write hardware. CSV exports the last 10,000 CAN records;
   the GUI displays the last 2,000 log lines. JSON structured logs go to stderr when running in a terminal.

Position is radians, velocity rad/s, current is filtered Iq in amperes, temperature °C.
State/fault bits come from status feedback; current is read independently, never inferred from torque.
The UI displays torque-derived status separately internally, and does not invent a temperature parameter.
Status/temperature are from the latest received status frame; parameter polling updates measured
position/velocity/current. Firmware-specific parameter support remains a hardware test item.

## Safety boundaries

Disconnect, application exit and worker exceptions attempt **zero velocity → disable → transport
close**, continuing cleanup after individual failures and reporting them. Motors discovered by scan
are cleanup targets even if the app has never enabled them. Software can address only discovered
motors (the smoke-test CLI additionally knows its specified ID). No broadcast stop is fabricated.
Commands have bounded response waits; USB operations are still subject to OS/driver timing.
A PC crash, killed process, USB unplug or motor power loss cannot guarantee delivery or stopping.
Use an accessible physical power cutoff, secure unloaded hardware, and verify a firmware CAN watchdog
separately before relying on this application. No automatic watchdog configuration is guessed.
Parameter persistence is not assumed; re-read after power cycling. This MVP does not send a save-to-flash
command or automatically set zero/calibrate motors.

## Architecture

- `src/transport`: physical gs_usb/SocketCAN, channel extension and FakeCanTransport.
- `src/protocol`: adapter around the official SDK with timeout/correlation fixes.
- `src/service`: configuration, structured/CAN logging, motor state and safety, dedicated QThread.
- `src/gui`: PySide6 controls; queued commands and signals only for CAN operations.
- `tests`: service, protocol, mocked USB and offscreen GUI/worker tests.

## Validate

Linux/cloud:

```sh
QT_QPA_PLATFORM=offscreen uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check src tests hardware_smoke_test.py desktop_main.py
uv run --frozen --extra dev mypy
uv run --frozen python hardware_smoke_test.py --backend simulated --csv /tmp/rs02-smoke.csv
```

PowerShell:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev mypy
Remove-Item Env:QT_QPA_PLATFORM
```

See [STATUS.md](STATUS.md) for evidence and the next hardware session's exact sequence.

## Windows executable

Build **on Windows x64 with Python 3.11**, from the repository root:

```powershell
uv sync --frozen --extra dev --python 3.11
uv run --frozen --extra dev pyinstaller --clean --noconfirm RS02Configurator.spec
.\dist\RS02Configurator\RS02Configurator.exe
```

Distribute the entire `dist\RS02Configurator` directory, including `_internal` and licenses.
This is an unsigned one-directory build. The spec bundles libusb-package's architecture-matched DLL;
no manual System32 DLL replacement is required. Building a Windows executable on Linux is unsupported.

## Linux physical backend

Configure SocketCAN using OS permissions before opening the application:

```sh
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
ip -details link show can0
```

Select physical SocketCAN and `can0`. Close other CAN tools first. Do not open gs_usb userspace
and SocketCAN against the same USB device simultaneously. SocketCAN does not change the bitrate
from the GUI; configure matching bitrate in the OS. Linux physical I/O is also unverified here.
