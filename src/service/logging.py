import csv
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Callable

import can

from transport.can_transport import CanTransport


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "time": self.formatTime(record),
                "level": record.levelname,
                "event": record.getMessage(),
            },
            ensure_ascii=False,
        )


class LoggedTransport:
    def __init__(self, inner: CanTransport, sink: Callable[[dict], None]):
        self.inner = inner
        self.sink = sink

    def log(self, direction: str, msg: can.Message) -> None:
        self.sink(
            {
                "time": time.time(),
                "direction": direction,
                "id": f"{msg.arbitration_id:08X}",
                "extended": msg.is_extended_id,
                "dlc": msg.dlc,
                "data": bytes(msg.data).hex(),
                "rx": msg.is_rx,
            }
        )

    def send(self, msg: can.Message) -> None:
        self.inner.send(msg)
        self.log("TX", msg)

    def recv(self, timeout: float | None = None) -> can.Message | None:
        msg = self.inner.recv(timeout)
        if msg is not None:
            self.log("RX", msg)
        return msg

    def shutdown(self) -> None:
        self.inner.shutdown()


class CanLog:
    def __init__(self, sink: Callable[[dict], None] | None = None):
        self.records: deque[dict] = deque(maxlen=10000)
        self.sink = sink

    def append(self, record: dict) -> None:
        self.records.append(record)
        logging.getLogger("rs02.can").info(json.dumps(record))
        if self.sink:
            self.sink(record)

    def export(self, path: str | Path) -> None:
        with Path(path).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["time", "direction", "id", "extended", "dlc", "data", "rx"]
            )
            writer.writeheader()
            writer.writerows(self.records)
