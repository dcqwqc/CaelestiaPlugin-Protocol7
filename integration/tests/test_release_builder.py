import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "prepare_native_release.py"
spec = importlib.util.spec_from_file_location("prepare_native_release", MODULE)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleaseBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "source"
        self.src.mkdir()

    def plugin(self, dirname, name="Sample", legacy=False):
        repo = self.src / dirname
        repo.mkdir()
        manifest = {
            "name": name, "author": "dcqwqc", "version": "1.2.3",
            "requires": ">=2.1.0",
            "entryPoints": [{"type": "custom", "source": "Main.qml"}]
        }
        (repo / "manifest.json").write_text(json.dumps(manifest))
        (repo / "Main.qml").write_text("import QtQuick\nItem {}\n")
        if legacy:
            original = {**manifest, "entryPoints": [
                {"type": "custom", "source": "Main.qml"},
                {"type": "shell-panel", "source": "Panel.qml"}]}
            original.pop("requires")
            (repo / "manifest.legacy.json").write_text(json.dumps(original))
            (repo / "Panel.qml").write_text("import QtQuick\nItem {}\n")
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "commit", "-qm", "Fixture"], check=True)
        return repo

    def test_two_profiles_keep_legacy_panels_separate(self):
        self.plugin("First", name="First", legacy=True)
        self.plugin("Second", name="Second")
        a = builder.build(self.src, self.root/"fork", "fork", expected=2)
        b = builder.build(self.src, self.root/"upstream", "upstream", expected=2)
        self.assertEqual(a["count"], 2)
        self.assertEqual(b["count"], 2)
        self.assertTrue(any(e["type"] == "shell-panel"
                            for e in json.loads((self.root/"fork/First/manifest.json").read_text())["entryPoints"]))
        self.assertFalse(any(e["type"] == "shell-panel"
                             for e in json.loads((self.root/"upstream/First/manifest.json").read_text())["entryPoints"]))

    def test_untracked_files_never_leave_source_checkout(self):
        repo = self.plugin("First")
        (repo / "private-local-only.txt").write_text("DONOTPACKAGE")
        builder.build(self.src, self.root/"out", "fork", expected=1)
        self.assertFalse((self.root/"out/First/private-local-only.txt").exists())

    def test_duplicate_identity_is_rejected(self):
        self.plugin("First", name="Same")
        self.plugin("Second", name="Same")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            builder.build(self.src, self.root/"out", "upstream", expected=2)
        self.assertFalse((self.root/"out").exists())

    def test_out_existing_refused_without_modification(self):
        self.plugin("First")
        dest = self.root/"out"
        dest.mkdir()
        marker = dest/"keep"
        marker.write_text("original")
        with self.assertRaises(FileExistsError):
            builder.build(self.src, dest, "fork", expected=1)
        self.assertEqual(marker.read_text(), "original")

    def test_symlink_outside_repo_is_rejected(self):
        repo = self.plugin("First")
        secret = self.root / "outside"
        secret.write_text("PRIVATE")
        link = repo / "escape"
        link.symlink_to(secret)
        subprocess.run(["git", "-C", str(repo), "add", "escape"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "commit", "-qm", "Symlink"], check=True)
        with self.assertRaisesRegex(ValueError, "external symlink"):
            builder.build(self.src, self.root/"out", "fork", expected=1)


if __name__ == "__main__":
    unittest.main()
