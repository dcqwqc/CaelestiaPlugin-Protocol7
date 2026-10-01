#!/usr/bin/env python3
import json
import os
import sys
import tempfile


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: plugin_config_bridge.py CONFIG_PATH PATCH_JSON")

    path = os.path.expanduser(sys.argv[1])
    patch = json.loads(sys.argv[2])

    os.makedirs(os.path.dirname(path), exist_ok=True)

    try:
        with open(path, "r", encoding="utf-8") as f:
            current = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        current = {}

    current.update(patch)

    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path),
        prefix=".protocol7-config.",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    main()
