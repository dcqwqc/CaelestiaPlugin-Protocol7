"""Regression tests for isolated native/fork plugin candidate packaging."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from integration.prepare_native_release import build


class CandidateBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="caelestia-release-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.sources = self.base / "sources"
        self.sources.mkdir()

    def repo(self, directory, name="Loom", author="dcqwqc", panel=False):
        root = self.sources / directory
        root.mkdir()
        manifest = {
            "name": name, "version": "0.2.0", "author": author,
            "requires": ">=2.1.0",
            "entryPoints": [{"type": "custom", "source": "Main.qml"}],
        }
        (root / "Main.qml").write_text("import QtQuick\nItem {}\n")
        (root / "manifest.json").write_text(json.dumps(manifest))
        if panel:
            legacy = dict(manifest)
            legacy.pop("requires")
            legacy["entryPoints"] = manifest["entryPoints"] + [
                {"type": "shell-panel", "source": "Panel.qml",
                 "properties": {"edge": "top"}}]
            (root / "Panel.qml").write_text("import QtQuick\nItem {}\n")
            (root / "manifest.legacy.json").write_text(json.dumps(legacy))
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        for key, val in [("user.name", "Caelestia CI"), ("user.email", "ci@example.invalid")]:
            subprocess.run(["git", "-C", str(root), "config", key, val], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
        return root

    def test_two_profiles_preserve_panel_functionality(self):
        self.repo("Loom", panel=True)
        native = self.base / "native"
        fork = self.base / "fork"
        assert build(self.sources, native, "upstream", expected=1)["count"] == 1
        assert build(self.sources, fork, "fork", expected=1)["count"] == 1
        n = json.loads((native / "Loom/manifest.json").read_text())
        f = json.loads((fork / "Loom/manifest.json").read_text())
        self.assertEqual(n["requires"], ">=2.1.0")
        self.assertNotIn("shell-panel", [e["type"] for e in n["entryPoints"]])
        self.assertIn("shell-panel", [e["type"] for e in f["entryPoints"]])
        self.assertFalse((self.sources / "Loom/.git").is_symlink())
        self.assertFalse((native / "Loom/.git").exists())
        self.assertEqual(json.loads((fork / "RELEASE_MANIFEST.json").read_text())["count"], 1)

    def test_output_is_never_overwritten(self):
        self.repo("One")
        output = self.base / "existing"
        output.mkdir()
        (output / "keep").write_text("original")
        with self.assertRaises(FileExistsError):
            build(self.sources, output, "fork", expected=1)
        self.assertEqual((output / "keep").read_text(), "original")

    def test_duplicate_identity_rejected_without_partial_publish(self):
        self.repo("One", name="Same")
        self.repo("Two", name="Same")
        output = self.base / "duplicate"
        with self.assertRaises(ValueError):
            build(self.sources, output, "upstream", expected=2)
        self.assertFalse(output.exists())

    def test_external_symlink_rejected(self):
        root = self.repo("One")
        outside = self.base / "private-data"
        outside.write_text("DO_NOT_PACKAGE")
        (root / "leak").symlink_to(outside)
        output = self.base / "external-link"
        with self.assertRaises(ValueError):
            build(self.sources, output, "upstream", expected=1)
        self.assertFalse(output.exists())

    def test_dirty_tracked_files_rejected(self):
        root = self.repo("One")
        (root / "Main.qml").write_text("Item { property string changed: 'local edit' }")
        with self.assertRaises(ValueError):
            build(self.sources, self.base / "dirty", "fork", expected=1)
        self.assertFalse((self.base / "dirty").exists())


if __name__ == "__main__":
    unittest.main()
