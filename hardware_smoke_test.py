"""Read-only physical smoke check (no enable/jog); cleanup still zeroes and disables."""

import argparse
import json

from service.config import Configuration
from service.motor import MotorService
from transport.can_transport import CanTransport, GsUsbTransport, SocketCanTransport
from transport.fake import FakeCanTransport


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", choices=["simulated", "gs_usb", "socketcan"], default="simulated")
    p.add_argument("--device", type=int, default=0, help="Enumerated USB device index")
    p.add_argument("--channel", type=int, choices=[0, 1], default=0)
    p.add_argument("--interface", default="can0")
    p.add_argument("--motor-id", type=int, default=127)
    p.add_argument("--list", action="store_true")
    p.add_argument("--csv", default="hardware-can-log.csv")
    args = p.parse_args()
    if not 1 <= args.motor_id <= 254:
        p.error("motor ID must be 1..254")
    if args.list:
        print(json.dumps(GsUsbTransport.enumerate_devices(), indent=2))
        return 0
    config = Configuration(backend=args.backend, channel=args.channel, socketcan=args.interface)
    service = MotorService(config)
    if args.backend == "gs_usb":
        devices = GsUsbTransport.enumerate_devices()
        if not 0 <= args.device < len(devices):
            p.error("USB device index unavailable; use --list first")
        transport: CanTransport = GsUsbTransport(devices[args.device], args.channel)
    elif args.backend == "socketcan":
        transport = SocketCanTransport(args.interface)
    else:
        transport = FakeCanTransport()
    errors: list[str] = []
    try:
        service.connect(transport)
        # Known candidate must also be stopped on failure, even if UUID reply is lost.
        service.safety_targets.add(args.motor_id)
        found = service.scan([args.motor_id])
        if args.motor_id not in found:
            raise RuntimeError("No motor reply. Hardware communication NOT verified.")
        print(
            json.dumps(
                {
                    "mode": args.backend,
                    "motors": found,
                    "telemetry": service.telemetry(args.motor_id),
                    "parameters": {
                        key: service.read(args.motor_id, key)
                        for key in ("mode", "current_limit", "velocity_limit", "acceleration")
                    },
                },
                indent=2,
            )
        )
    finally:
        errors = service.disconnect()
        service.log.export(args.csv)
    if errors:
        raise RuntimeError("; ".join(errors))
    print(
        "Simulated check passed."
        if args.backend == "simulated"
        else "Physical replies received for this run; motion remains untested."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
