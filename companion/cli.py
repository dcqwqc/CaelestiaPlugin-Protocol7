#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from companion.ipc import send_command
from companion.state import VALID_STATES, VALID_SHAPES


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="hey-tabby", description="Control the local Hey Tabby companion HUD")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    p = sub.add_parser("state")
    p.add_argument("value", choices=sorted(VALID_STATES))
    sub.add_parser("show")
    sub.add_parser("hide")
    sub.add_parser("clear")
    p = sub.add_parser("text")
    p.add_argument("text")
    p.add_argument("--title", default="")
    p = sub.add_parser("progress")
    p.add_argument("value", type=float)
    p.add_argument("--label", default="")
    p = sub.add_parser("choice")
    p.add_argument("label")
    p.add_argument("options", nargs="*")
    p = sub.add_parser("shape")
    p.add_argument("kind", choices=sorted(VALID_SHAPES))
    p.add_argument("x", type=float)
    p.add_argument("y", type=float)
    p.add_argument("w", type=float)
    p.add_argument("h", type=float)
    p.add_argument("--label", default="")
    return root


def main() -> int:
    args = parser().parse_args()
    command = {"command": args.command}
    if args.command == "state":
        command["value"] = args.value
    elif args.command == "text":
        command.update(text=args.text, title=args.title)
    elif args.command == "progress":
        command.update(value=args.value, label=args.label)
    elif args.command == "choice":
        command.update(label=args.label, options=args.options)
    elif args.command == "shape":
        command.update(kind=args.kind, x=args.x, y=args.y, w=args.w, h=args.h, label=args.label)
    result = send_command(command)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
