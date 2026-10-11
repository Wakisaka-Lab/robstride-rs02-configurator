# Development instructions

Use the existing checkout; each cloud task is already isolated. Do not create a worktree unless
explicitly requested. Target Python 3.11, Windows 10/11 x64; Linux is for simulation and optional
SocketCAN. Use `uv sync --frozen --extra dev --python 3.11` (or the existing `.venv`).

Required checks from the repository root:

```sh
QT_QPA_PLATFORM=offscreen uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check src tests hardware_smoke_test.py desktop_main.py
uv run --frozen --extra dev mypy
uv run --frozen python hardware_smoke_test.py --backend simulated --csv /tmp/rs02-smoke.csv
```

CAN I/O, discovery and CSV export belong to the worker; never put CAN I/O in Qt event handlers.
Do not automatically connect or enable on startup or JSON load. Keep hold-only jog, independent
jog lease, explicit ID confirmation, and best-effort zero/disable/close ordering.
Keep vendored SDK files byte-identical and preserve the license. Use source-backed changes with
fixtures when adapting protocol behavior; do not invent frames or use simulator tests as hardware
proof. Document uncertainties and actual results in STATUS.md. Never enable motors in cloud tests.
Windows packaging must run on Windows; do not claim a Linux bundle is a tested Windows EXE.
