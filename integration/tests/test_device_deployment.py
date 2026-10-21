import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/"device_deploy_fork.py"
spec=importlib.util.spec_from_file_location("device_deploy_fork",SCRIPT)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class DeviceDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        h=Path(self.temp.name)
        mod.HOME=h
        mod.LIVE=h/"plugins"
        mod.BASE=h/"state"
        mod.SRC=mod.BASE/"staging"
        mod.RELEASE=mod.BASE/"candidate"
        mod.BACKUP=mod.BASE/"backup"
        mod.JOURNAL=mod.BACKUP/"transaction.json"
        mod.LIVE.mkdir(parents=True)
        mod.SRC.mkdir(parents=True)
        records=[]
        for i in range(32):
            m={"author":"demo", "name":f"Plugin{i}", "version":"1.0.0",
               "entryPoints":[]}
            n=f"p{i:02}"
            old=mod.LIVE/n
            if i==0:
                project=h/"original-project"
                project.mkdir()
                (project/"manifest.json").write_text(json.dumps(m))
                old.symlink_to(project)
            else:
                old.mkdir()
                (old/"manifest.json").write_text(json.dumps(m))
            (old/"local-only.txt").write_text("DO NOT DELETE")
            new=mod.SRC/n
            new.mkdir()
            source=(new/"manifest.json")
            source.write_text(json.dumps(m))
            (new/"candidate-only.txt").write_text("MIGRATION")
            records.append({"plugin_id":f"demo/plugin{i}",
                            "manifest_sha256":hashlib.sha256(source.read_bytes()).hexdigest()})
        (mod.SRC/"RELEASE_MANIFEST.json").write_text(
            json.dumps({"profile":"fork","count":32,"plugins":records}))
        mod.prepare()

    def test_rollback_preserves_original_symlink_and_files(self):
        mod.activate()
        self.assertTrue((mod.LIVE/"p00").is_symlink())
        self.assertEqual((mod.LIVE/"p00/candidate-only.txt").read_text(),"MIGRATION")
        mod.rollback()
        self.assertTrue((mod.LIVE/"p00").is_symlink())
        self.assertEqual((mod.LIVE/"p00").resolve().name,"original-project")
        self.assertTrue((mod.LIVE/"p00/local-only.txt").is_file())
        self.assertFalse((mod.LIVE/"p00/candidate-only.txt").exists())

    def test_guard_restores_pending_deployment(self):
        mod.activate()
        mod.guard()
        j=json.loads(mod.JOURNAL.read_text())
        self.assertEqual(j["status"],"rolled_back")
        self.assertFalse((mod.LIVE/"p12").is_symlink())

    def test_confirm_prevents_guarded_rollback(self):
        mod.activate()
        mod.confirm()
        mod.guard()
        self.assertEqual(json.loads(mod.JOURNAL.read_text())["status"],"active_verified")
        self.assertTrue((mod.LIVE/"p12").is_symlink())

    def test_manifest_change_blocks_activation_without_mutation(self):
        (mod.LIVE/"p04/manifest.json").write_text(
            '{"name":"Changed","author":"demo"}')
        with self.assertRaises(Exception):
            mod.activate()
        self.assertFalse(mod.JOURNAL.exists())
        self.assertFalse((mod.LIVE/"p12").is_symlink())


if __name__=="__main__":
    unittest.main()
