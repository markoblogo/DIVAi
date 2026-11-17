"""Regression checks for role selection and repeatable filming without a speaker."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import shoot
import studio


class RoleTests(unittest.TestCase):
    def test_default_and_shortcuts_with_legacy_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefs = root / "preferences.json"
            prefs.write_text(json.dumps({"face": "crt", "side": "right"}))
            with patch.object(studio, "PROJECT", root), patch.object(
                studio, "preference_file", return_value=prefs
            ), patch.object(sys, "argv", ["studio.py", "--preview"]), patch.object(
                studio.Studio, "run", autospec=True
            ) as run:
                self.assertEqual(studio.main(), 0)
                session = run.call_args.args[0]
                self.assertEqual(session.face, "observer")
                session.key("h")
                self.assertTrue(session.show_clock)
                session.key("s")
                self.assertEqual(session.face, "director")
                self.assertFalse(session.show_clock)
                session.key("d")
                self.assertEqual(session.face, "observer")
                self.assertEqual(session.settings["crt"][0], "right")

    def test_role_file_supersedes_legacy_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = {"version": 1, "face": "director", "side": "left", "changes": {}}
            (root / "character.json").write_text(json.dumps(legacy))
            with patch.object(studio, "PROJECT", root):
                self.assertEqual(studio.load_skins()["director"].side, "left")
                (root / "director.json").write_text(json.dumps(dict(legacy, side="right")))
                self.assertEqual(studio.load_skins()["director"].side, "right")


class FilmingTests(unittest.TestCase):
    def test_single_reaction_per_take_and_stale_stop(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            shoot, "preference_file", return_value=Path(directory) / "preferences.json"
        ), patch.object(shoot.time, "monotonic", return_value=10.0) as clock:
            player = shoot.Shoot(device=None)
            first = {"id": "a" * 24, "line": "Cut."}
            second = {"id": "b" * 24, "line": "Again."}
            self.assertEqual(player.command("prepare", first)["phase"], "armed")
            self.assertEqual(player.command("fire", first)["frame"], "camera")
            clock.return_value = 10.5
            player.tick()
            self.assertEqual(player.phase, "talking")
            clock.return_value = 12.0
            player.tick()
            self.assertEqual(player.frame, "look")
            self.assertEqual(player.command("fire", first)["phase"], "done")

            self.assertEqual(player.command("prepare", second)["phase"], "armed")
            self.assertEqual(player.command("stop", first)["id"], second["id"])
            self.assertEqual(player.command("fire", second)["phase"], "camera")
            self.assertEqual(player.command("stop", second)["phase"], "idle")
            clock.return_value = 20.0
            player.tick()
            self.assertIsNone(player.take)
            self.assertEqual(player.frame, "look")
