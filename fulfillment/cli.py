"""命令行入口：python -m fulfillment.cli reseed"""
from __future__ import annotations

import argparse
from pathlib import Path

from .seed import seed

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description="手作履约系统命令行")
    ap.add_argument("command", choices=["reseed"], help="reseed=重建演示数据")
    ap.add_argument("--db", default=str(ROOT / "fulfillment" / "fulfillment.db"))
    ap.add_argument("--photos", default=str(ROOT / "fulfillment" / "photos"))
    ap.add_argument("--img", default=str(ROOT / "fulfillment" / "web" / "static" / "img"))
    args = ap.parse_args()

    if args.command == "reseed":
        seed(args.db, args.photos, args.img)
        print(f"演示数据已重建: {args.db}")


if __name__ == "__main__":
    main()
