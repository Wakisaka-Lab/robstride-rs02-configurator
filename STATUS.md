# Implementation status

Recorded 2026-10-12 (Asia/Tokyo). Next hardware session: **2026-10-13**, if testing tomorrow.

## Completed functions

- Python 3.11 / PySide6 desktop application and installable project with locked dependencies.
- Disconnected startup; explicit simulation/physical labels; no automatic enable or JSON auto-connect.
- gs_usb-compatible USB enumeration, device and CAN0/CAN1 selection, bitrate selection (default 1 Mbit/s).
- Windows gs_usb transport based on python-can, per-channel extension from the manufacturer driver,
  architecture-matched libusb-package; optional Linux SocketCAN and FakeCanTransport.
- Official Python SDK vendored unchanged with license, commit and hashes; transport abstraction,
  bounded correlated receive, source-backed CAN ID correction, parameter and status decoding.
- Dedicated CAN worker QThread; cancellable scan, queued command handling and priority stop/disconnect.
- Scan IDs 1–254; UUID, state/faults, position/velocity/current/temperature display.
- Supported parameter reads, restricted writes with readback, mode/limits/acceleration configuration.
- Explicit confirmed single-motor ID change, collision query, new-ID UUID check and ambiguous-ID cleanup.
- Explicit enable, disable/stop, conservative defaults and mode-specific neutral targets before enabling.
- Forward/reverse hold-only velocity jog; pre-send hold recheck, release zero request, focus-loss stop,
  350 ms GUI-heartbeat lease expiry, and no queued jog after a stop/disconnect.
- Disconnect/exit/worker-exception zero-velocity request → disable attempt → transport close;
  cleanup continues after errors and reports failures. Unhandled GUI exceptions request disconnect.
- Structured JSON logging, bounded CAN TX/RX GUI log and CSV export, validated versioned JSON config.
- Safe smoke-test CLI, Windows PyInstaller spec, driver/setup guide and development instructions.

## Verification results

Actual cloud execution used **Python 3.11.17 on Linux**:

| Check | Result |
| --- | --- |
| Complete automated suite (`QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q`) | **56 passed**, no skips or expected failures |
| `ruff check .` | Passed |
| `ruff format --check src tests hardware_smoke_test.py desktop_main.py` | Passed |
| `mypy` | Passed, 14 source files |
| Simulated `hardware_smoke_test.py` | Passed; UUID/parameters/telemetry returned; no enable; CSV written |
| Qt GUI startup/rendering | Passed offscreen; simulated GUI press/release and closing exercised |
| Official SDK source hash check | Passed, all four Python files unchanged |
| Mocked USB channel control/TX/RX and backend construction | Passed for CAN0 and CAN1 |
| Dependency install repeatability (`uv sync --frozen`) | Passed |
| Source distribution and wheel build | Passed; wheel includes official SDK license |
| Physical USB enumeration / transport startup | **Not verified**; cloud lacks USB device access |
| Actual Windows executable build / launch | **Not run**; requires Windows x64 |
| RS02 physical communications or motion | **Not run**; no USB2CAN-X2 or RS02 attached |

Tests include scan and quiescing an already-running simulated motor; parameter read/write/validation;
source-backed wire fixtures; confirmation/collision/ambiguous ID handling; enable/disable; both jog
directions and release; release during delayed preflight; zero/disable/close ordering on disconnect
and exit; cleanup failures; CAN worker thread ownership; exception shutdown; priority stop/disconnect; cancellation of enable during configuration;
lease expiry; Qt confirmation cancellation; JSON/CSV; USB channel mapping and unplug-error propagation.
The simulator and mocked USB do **not** prove physical protocol/firmware compatibility.

## Unverified hardware assumptions and limits

- X2 firmware matches a VID/PID recognized by gs-usb 0.3.0, reports `icount + 1` channels and uses
  manufacturer gs_usb control `wValue` / frame.channel mapping. Physical CAN0/CAN1 labels need checking.
- Windows Zadig libusbK binding, Python x64/libusb DLL load, exclusive USB ownership and PyInstaller
  DLL inclusion need validation on the target Windows machine. Cloud libusb loads but cannot initialize
  a USB backend: `/dev/bus/usb` and `/sys/bus/usb/devices` are absent. No-driver/no-device is not proof
  that physical transport works. Linux SocketCAN physical operation is also unverified.
- RS02 is powered/wired/terminated correctly, uses the private RobStride protocol, 29-bit classical CAN
  and 1 Mbit/s. The app does not guess protocol switching or transmit arbitrary probe frames.
- Firmware supports the documented SDK parameter indices, especially acceleration `0x7022`, and the
  chosen modes (0 MIT, 1 position, 2 velocity, 3 current). Write status acknowledgements and readback
  behavior, UUID response destination (`0xFE` or host) and CAN-ID change need physical confirmation.
- State/fault decoding and model scaling follow the pinned SDK; temperature/status reflect the latest
  status frame. Parameter polling does not guarantee a new temperature/state sample on every poll.
- Limits are conservative application defaults, not a guaranteed safety envelope. Parameter persistence
  and motor CAN watchdog behavior have not been established. No flash save/watchdog setup is fabricated.
- USB unplug, process termination, OS hangs, power loss and undeliverable commands cannot ensure a stop.
  Software cleanup covers known IDs only and reports best-effort failures. Have an independent cutoff.
- The Windows EXE is configured but has not been built/tested; no cross-platform binary claim is made.

## Exact next-session hardware checklist

Follow `docs/windows_setup.md` first. Record every step's result rather than marking this list complete
based on simulation. Do not progress to enable or jog after any earlier unexplained failure.

1. On Windows 10/11 x64, install Python 3.11 and uv. In the checkout run:
   `uv sync --frozen --extra dev --python 3.11`.
   Run `uv run --frozen --extra dev pytest -q` with `$env:QT_QPA_PLATFORM="offscreen"`, then remove
   that environment variable. Run `ruff check .` and `mypy` through `uv run --frozen --extra dev`.
2. With motor power OFF, plug in X2. Close all vendor/other CAN clients. Record serial, VID/PID,
   firmware version if available and current Windows driver. Use Zadig to bind the correctly identified
   X2 USB interface to **libusbK**; do not bind an unrelated device or select libusb-win32.
3. Run `uv run --frozen python hardware_smoke_test.py --list`. Record enumerated product/serial,
   device index and USB bus/address. Resolve driver/DLL/access issues if no usable entry appears.
4. Wire one secured, unloaded RS02 to **labelled CAN0** using the manufacturer pinout; verify power
   polarity, CAN_H/L, reference/ground and termination while unpowered. Provide a physical cutoff.
   Confirm the motor is configured for private protocol, extended classical CAN and **1 Mbit/s**.
5. Power the motor. For the known motor ID (replace 127 if different), run:
   `uv run --frozen python hardware_smoke_test.py --backend gs_usb --device 0 --channel 0 --motor-id 127 --csv rs02-can0.csv`.
   The CLI sends UUID/parameter requests and zero/disable, never enable. Require a consistent UUID,
   plausible position/current/voltage/temperature, parameter replies and successful disabled cleanup.
6. Power OFF; move wires to **labelled CAN1**. Repeat step 5 with `--channel 1 --csv rs02-can1.csv`.
   Record channel correspondence. Restore desired channel only while unpowered.
7. Launch `uv run --frozen rs02-configurator`. Confirm disconnected startup. Select physical gs_usb,
   enumerate/select the X2, select the verified channel and 1 Mbit/s. Connect/scan; verify UUID and
   disabled state. Read parameters, save JSON, disconnect, reload JSON and confirm no auto-connect.
8. Connect/scan again. While disabled, apply mode 2, **0.5 A current**, **0.5 rad/s velocity**,
   **0.5 rad/s² acceleration**, **0.2 rad/s jog**. Require matching readback. If acceleration or another
   parameter is unsupported, record firmware/response and stop; do not bypass checks or guess an index.
9. With the shaft clear and cutoff ready, explicitly Enable. Confirm reported running state with zero
   measured velocity and reasonable current. Hold forward briefly (<0.5 s), release and verify zero
   target/velocity. Repeat reverse. Click Stop and verify zero target plus disabled state. Record signs,
   units, motion, current and temperature; stop immediately on faults or unexpected movement.
10. Re-enable/briefly jog, then verify focus-loss stop, Disconnect stop and window-close stop separately.
    Check transmitted zero then disable and actual physical stopping. Test unplug only with a safe,
    secured setup and power cutoff; delivery cannot be guaranteed and unplug is not a successful stop test.
11. With motor disabled and **only one motor physically connected**, choose a spare ID (e.g. 128),
    confirm the explicit warning, change ID and verify the **same UUID** at the new address. Rescan.
    Return to the original ID if desired. Power cycle and record whether the ID/settings persist;
    persistence is not assumed. Do not ID-change a powered multi-motor bus.
12. Export GUI CAN CSV and retain CLI CSVs plus notes (driver/firmware, wiring, ID, channel and faults).
    Build on Windows with `uv run --frozen --extra dev pyinstaller --clean --noconfirm RS02Configurator.spec`.
    Launch `dist\RS02Configurator\RS02Configurator.exe`; confirm simulated startup, physical enumeration,
    libusb loading and safe close. Distribute the entire directory. Record binary build/launch separately.

Cloud source implementation and simulation are complete. Physical readiness remains conditional on the
above hardware/Windows checks; this document does not assert those checks have passed.

## Reusable cloud configuration

`install_script` and `start_skill` were saved successfully to the environment configuration draft.
The installation script was exercised on this instance, including repeat `uv sync --frozen`.
Saving did not publish or apply a new environment version. Review/save the draft in environment
settings and Publish when desired. A new-task snapshot restoration has not been tested.
