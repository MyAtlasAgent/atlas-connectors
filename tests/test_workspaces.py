"""Shared workspace folders: names, base resolution, registry, default."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlas_connectors import workspaces as ws
from atlas_connectors.adapters import AdapterError
from atlas_connectors.cli import initialize, main, route_for
from atlas_connectors.permissions import load_configuration

ROOT = Path(__file__).resolve().parents[1]


class Names(unittest.TestCase):
    def test_valid_and_invalid_names(self):
        for good in ("Team Project", "q4-plan_v2", "ünïcode"):
            self.assertEqual(ws.validate_name(good), good)
        for bad in ("", " ", "a/b", "a\\b", "..", "x..y", "CON", "nul.txt", "Lpt1", "trail.", "trail ",
                    " lead", "a:b", "a*b", "x" * 65, "tab\there"):
            with self.subTest(bad=bad), self.assertRaises(AdapterError):
                ws.validate_name(bad)


class Base(unittest.TestCase):
    def test_windows_known_folder_follows_onedrive_redirect(self):
        redirected = r"C:\Users\<user>\OneDrive\Documents"
        base = ws.default_base(platform="win32", environ={"USERPROFILE": r"C:\Users\<user>"},
                               known_folder=lambda: redirected)
        self.assertEqual(base, Path(redirected) / "Atlas Workspaces")
        fallback = ws.documents_dir(platform="win32", environ={"USERPROFILE": "/profile"}, known_folder=lambda: None)
        self.assertEqual(fallback, Path("/profile/Documents"))

    def test_macos_and_linux_bases(self):
        self.assertEqual(ws.documents_dir(platform="darwin", environ={}, home="/h"), Path("/h/Documents"))
        self.assertEqual(ws.documents_dir(platform="linux", environ={"XDG_DOCUMENTS_DIR": "/docs"}, home="/h"),
                         Path("/docs"))
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".config").mkdir()
            (Path(home) / ".config/user-dirs.dirs").write_text('XDG_DOCUMENTS_DIR="$HOME/Dokumente"\n')
            self.assertEqual(ws.documents_dir(platform="linux", environ={}, home=home), Path(home) / "Dokumente")
            self.assertEqual(ws.documents_dir(platform="linux", environ={"XDG_CONFIG_HOME": "/none"}, home=home),
                             Path(home) / "Documents")


class Create(unittest.TestCase):
    def setUp(self):
        base = tempfile.TemporaryDirectory(dir=ROOT, prefix=".test-base-")
        data = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        self.addCleanup(data.cleanup)
        self.base, self.data = Path(base.name).resolve(), Path(data.name)

    def test_create_layout_metadata_and_registry(self):
        result = ws.create("Team", base=self.base, root=self.data)
        folder = self.base / "Team"
        self.assertEqual(result["path"], str(folder))
        for name in ("README.md", ".atlas-workspace.json", "notes", "handoffs"):
            self.assertTrue((folder / name).exists(), name)
        self.assertFalse((folder / "inbox").exists())
        self.assertFalse((folder / "CLAUDE.md").exists())
        meta = json.loads((folder / ".atlas-workspace.json").read_text())
        self.assertEqual(set(meta), {"name", "created_at", "participants"})
        self.assertEqual(ws.registered(self.data)["Team"]["path"], str(folder))

    def test_exist_ok_and_temp_refusal(self):
        ws.create("Team", base=self.base, root=self.data)
        with self.assertRaisesRegex(AdapterError, "exist-ok"):
            ws.create("Team", base=self.base, root=self.data)
        ws.create("Team", base=self.base, root=self.data, exist_ok=True)
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(AdapterError, "temp"):
            ws.create("Team", base=temporary, root=self.data)
        with self.assertRaises(AdapterError):
            ws.create("Team", base="relative", root=self.data)

    def test_agent_instructions_only_inside_new_folder(self):
        before = set(self.base.parent.iterdir())
        result = ws.create("Team", base=self.base, root=self.data, with_agent_instructions=True)
        self.assertEqual(result["agent_instructions"], ["CLAUDE.md", "AGENTS.md"])
        for name in ("CLAUDE.md", "AGENTS.md"):
            text = (self.base / "Team" / name).read_text()
            self.assertIn("atlas-connectors peers", text)
            self.assertIn("mail inbox", text)
            self.assertIn("handoffs/", text)
            self.assertIn("untrusted", text)
        self.assertEqual(set(self.base.parent.iterdir()), before)
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["Team"])

    def test_set_default_list_use_and_send_resolution(self):
        with tempfile.TemporaryDirectory() as cfg:
            config = initialize(cfg)["configuration"]
            with patch("sys.stdout"):
                self.assertEqual(main(["workspace", "create", "Alpha", "--base", str(self.base), "--set-default",
                                       "--config", config, "--data-dir", str(self.data)]), 0)
                self.assertEqual(main(["workspace", "create", "Beta", "--base", str(self.base),
                                       "--data-dir", str(self.data)]), 0)
            configuration = load_configuration(config)
            self.assertEqual(route_for(configuration).workspace, str(self.base / "Alpha"))
            self.assertEqual(set(ws.registered(self.data)), {"Alpha", "Beta"})
            ws.use("Beta", config, self.data)
            configuration = load_configuration(config)
            self.assertEqual(route_for(configuration).workspace, str(self.base / "Beta"))
            self.assertEqual(route_for(configuration, "claude_app").workspace, str(self.base / "Beta"))
            with self.assertRaises(AdapterError):
                ws.use("Gamma", config, self.data)


if __name__ == "__main__":
    unittest.main()
