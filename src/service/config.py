import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from robstride_dynamics.table import MODEL_MIT_VELOCITY_TABLE

# Application ceilings. SDK MIT feedback range is a reference, not a firmware guarantee.
MAX_RS02_VELOCITY = float(MODEL_MIT_VELOCITY_TABLE["rs-02"])
SETTING_LIMITS = {
    "current_limit": 2.0,
    "velocity_limit": MAX_RS02_VELOCITY,
    "acceleration": 2.0,
    "jog_velocity": MAX_RS02_VELOCITY,
}


@dataclass
class Configuration:
    version: int = 1
    backend: str = "simulated"
    bitrate: int = 1_000_000
    channel: int = 0
    socketcan: str = "can0"
    current_limit: float = 0.5
    velocity_limit: float = 0.5
    acceleration: float = 0.5
    jog_velocity: float = 0.2
    mode: int = 2

    def validate(self) -> None:
        for name in ("version", "bitrate", "channel", "mode"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if self.version != 1 or self.backend not in ("simulated", "gs_usb", "socketcan"):
            raise ValueError("Unsupported configuration")
        if self.bitrate not in (125000, 250000, 500000, 1000000):
            raise ValueError("Unsupported bitrate")
        if type(self.channel) is not int or not 0 <= self.channel <= 1:
            raise ValueError("Channel must be 0 or 1")
        if self.mode not in (0, 1, 2, 3):
            raise ValueError("Unsupported mode")
        for name, ceiling in SETTING_LIMITS.items():
            value = getattr(self, name)
            if (
                type(value) not in (float, int)
                or not math.isfinite(value)
                or not 0 < value <= ceiling
            ):
                raise ValueError(f"{name} must be >0 and <= {ceiling}")
        if self.jog_velocity > self.velocity_limit:
            raise ValueError("Jog exceeds velocity limit")
        if not isinstance(self.socketcan, str) or not self.socketcan:
            raise ValueError("SocketCAN interface required")

    def save(self, path: str | Path) -> None:
        self.validate()
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Configuration":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Configuration must be an object")
        result = cls(**raw)
        result.validate()
        return result
