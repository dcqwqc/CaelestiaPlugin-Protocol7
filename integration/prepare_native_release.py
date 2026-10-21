#!/usr/bin/env python3
"""Build isolated Caelestia plugin candidate trees without changing running plugins."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENTRY_TYPES = {"custom", "bar-entry", "bar-popout", "quick-toggle", "status-icon", "dashboard-tab"}
FORK_TYPES = ENTRY_TYPES | {"shell-panel"}
IGNORED = {".git", ".github", "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", "node_modules", ".ruff_cache"}

def safe_copy(src: Path, dest: Path) -> None:
    base = src.resolve()
    for path in src.rglob("*"):
        if any(part in IGNORED for part in path.relative_to(src).parts):
            continue
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(base):
                raise ValueError(f"external symlink is not safe to package: {path}")
    shutil.copytree(src, dest, symlinks=False,
                    ignore=lambda folder, names: [name for name in names if name in IGNORED])

def manifest_for(repo: Path, profile: str) -> dict:
    native = json.loads((repo / "manifest.json").read_text(encoding="utf-8"))
    if profile == "fork":
        if (repo / "manifest.legacy.json").exists():
            selected = json.loads((repo / "manifest.legacy.json").read_text(encoding="utf-8"))
        else:
            selected = dict(native)
            # The existing fork already has the native loader; its version need not
            # pretend to be the as-yet unreleased upstream 2.1.0.
            selected.pop("requires", None)
    else:
        selected = native
        if selected.get("requires") != ">=2.1.0":
            raise ValueError(f"{repo.name}: native development gate missing")
    if selected.get("name") != native.get("name") or selected.get("author") != native.get("author"):
        raise ValueError(f"{repo.name}: profile changes plugin identity")
    return selected

def verify_manifest(repo: Path, m: dict, profile: str) -> list[str]:
    for field in ("author", "name"):
        if not isinstance(m.get(field), str) or not NAME.fullmatch(m[field]):
            raise ValueError(f"{repo.name}: invalid {field}")
    if not isinstance(m.get("version"), str) or not re.fullmatch(r"[0-9][0-9A-Za-z._+-]*", m["version"]):
        raise ValueError(f"{repo.name}: invalid version")
    allowed = FORK_TYPES if profile == "fork" else ENTRY_TYPES
    declared = []
    for entry in m.get("entryPoints", []):
        kind = entry.get("type")
        source = entry.get("source")
        if kind not in allowed:
            raise ValueError(f"{repo.name}: unsupported {kind} in {profile} profile")
        if not isinstance(source, str) or not source:
            raise ValueError(f"{repo.name}: missing entry point source")
        path = Path(source)
        if path.is_absolute() or ".." in path.parts or not (repo / path).is_file():
            raise ValueError(f"{repo.name}: missing/unsafe entry point {source}")
        declared.append(kind)
    for field in ("settings", "settingsUi"):
        if field in m:
            p = Path(m[field])
            if p.is_absolute() or ".." in p.parts or not (repo / p).is_file():
                raise ValueError(f"{repo.name}: missing settings component {m[field]}")
    return declared

def build(root: Path, output: Path, profile: str, expected: int = 29) -> dict:
    root = root.resolve(strict=True)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite {output}")
    if output.is_relative_to(root):
        raise ValueError("Output must be outside source checkout root")
    repos = sorted(p for p in root.iterdir() if (p / ".git").exists())
    if len(repos) != expected:
        raise ValueError(f"Expected {expected} repositories, found {len(repos)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".caelestia-assemble-", dir=output.parent))
    ids = set()
    index = []
    try:
        for src in repos:
            if subprocess.run(["git", "-C", str(src), "diff", "--quiet"]).returncode != 0:
                raise ValueError(f"Working tree has tracked modifications: {src.name}")
            if subprocess.run(["git", "-C", str(src), "diff", "--cached", "--quiet"]).returncode != 0:
                raise ValueError(f"Index has tracked modifications: {src.name}")
            manifest = manifest_for(src, profile)
            kinds = verify_manifest(src, manifest, profile)
            plugin_id = f"{manifest['author']}/{manifest['name']}".lower()
            if plugin_id in ids:
                raise ValueError(f"Duplicate plugin identity: {plugin_id}")
            ids.add(plugin_id)
            dest = tmp / src.name
            safe_copy(src, dest)
            data = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
            (dest / "manifest.json").write_bytes(data)
            commit = subprocess.check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip()
            index.append({"repo": f"dcqwqc/CaelestiaPlugin-{src.name}",
                          "commit": commit, "plugin_id": plugin_id,
                          "manifest_sha256": hashlib.sha256(data).hexdigest(),
                          "entry_types": kinds,
                          "needs_custom_host": ("shell-panel" in kinds or
                                               any(e.get("properties", {}).get("slot") for e in manifest.get("entryPoints", [])))})
        document = {"profile": profile, "count": len(index),
                    "production_installation_modified": False, "plugins": index}
        (tmp / "RELEASE_MANIFEST.json").write_text(json.dumps(document, indent=2) + "\n")
        os.rename(tmp, output)
        return document
    except Exception:
        shutil.rmtree(tmp)
        raise

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--profile", required=True, choices=["fork", "upstream"])
    args = p.parse_args()
    report = build(args.source, args.output, args.profile)
    print(json.dumps({"output": str(args.output), "profile": args.profile,
                      "count": report["count"], "custom_host_repos":
                          [x["repo"] for x in report["plugins"] if x["needs_custom_host"]]}, indent=2))

if __name__ == "__main__":
    main()
