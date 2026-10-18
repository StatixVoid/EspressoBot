import hashlib
import importlib.util
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("espresso_launcher", Path(__file__).resolve().parents[1] / "scripts" / "launch.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_cwd = Path.cwd()
        (self.root / "requirements.txt").write_text("test-lib==1\n")
        (self.root / ".env.example").write_text("DISCORD_TOKEN=\n")
        python = self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.touch()

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.temp.cleanup()

    def test_bootstrap_installs_once_and_preserves_existing_secrets(self):
        config = self.root / ".env"
        config.write_text("DISCORD_TOKEN=existing-private-value\n")
        with patch.object(launcher,"ROOT",self.root),patch("sys.argv",["launch.py","--setup"]),patch.object(launcher.subprocess,"run") as install:
            self.assertEqual(launcher.main(),0)
            self.assertEqual(launcher.main(),0)
        install.assert_called_once()
        self.assertEqual(config.read_text(),"DISCORD_TOKEN=existing-private-value\n")

    def test_config_creation_and_argument_forwarding(self):
        marker = self.root / ".venv/.espresso-requirements.sha256"
        marker.write_text(hashlib.sha256((self.root/"requirements.txt").read_bytes()).hexdigest())
        with patch.object(launcher,"ROOT",self.root),patch("sys.argv",["launch.py","--web-only"]),patch.object(launcher.subprocess,"call",return_value=0) as run:
            self.assertEqual(launcher.main(),0)
            self.assertEqual(run.call_args.args[0][-3:],["-m","espresso","--web-only"])
        self.assertEqual((self.root/".env").read_text(),"DISCORD_TOKEN=\n")
