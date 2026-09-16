"""Factory provisioning tool for the sealed commissioning QR card.

Usage:
  qr_factory.py provision --device-id DEVICE_RK3576_01 \
      --product-model EGO-RK3576 --software-version 0.3.0 \
      [--discriminator A1B] [--identity-path /etc/ego/ble/device-identity.json] \
      [--qr-out qr-card.json] [--png-out qr-card.png]
  qr_factory.py show --identity-path /etc/ego/ble/device-identity.json

The QR payload is printed to stdout exactly as encoded on the sealed card.
PNG output requires the optional ``qrcode`` package; without it the factory
still emits the canonical JSON payload for external card printing.
"""

from __future__ import annotations

import argparse
import json
import sys

from crypto_glue import CommissioningError
from identity import generate_identity, load_identity, save_identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qr_factory")
    sub = parser.add_subparsers(dest="command", required=True)

    provision = sub.add_parser("provision", help="create a new device identity")
    provision.add_argument("--device-id", required=True)
    provision.add_argument("--product-model", required=True)
    provision.add_argument("--software-version", required=True)
    provision.add_argument("--discriminator", default=None)
    provision.add_argument("--identity-path", default="/etc/ego/ble/device-identity.json")
    provision.add_argument("--qr-out", default=None, help="write QR payload JSON to a file")
    provision.add_argument("--png-out", default=None, help="write QR card PNG (needs qrcode)")

    show = sub.add_parser("show", help="print the QR payload of an existing identity")
    show.add_argument("--identity-path", default="/etc/ego/ble/device-identity.json")
    show.add_argument("--png-out", default=None)

    args = parser.parse_args(argv)

    try:
        if args.command == "provision":
            identity = generate_identity(
                args.device_id, args.product_model, args.software_version,
                discriminator=args.discriminator,
            )
            save_identity(identity, args.identity_path)
        else:
            identity = load_identity(args.identity_path)
    except CommissioningError as exc:
        print(f"error: {exc.code}", file=sys.stderr)
        return 2

    payload = json.dumps(identity.qr_payload(), separators=(",", ":"), ensure_ascii=False)
    print(payload)
    if getattr(args, "qr_out", None):
        with open(args.qr_out, "w") as handle:
            handle.write(payload + "\n")
    if getattr(args, "png_out", None):
        try:
            import qrcode
        except ImportError:
            print("qrcode package unavailable; print the JSON payload externally",
                  file=sys.stderr)
            return 1
        image = qrcode.make(payload)
        image.save(args.png_out)
        print(f"PNG written: {args.png_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
