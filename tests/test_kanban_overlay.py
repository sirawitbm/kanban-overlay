import json
import os
import re
import sys
import tempfile
import unittest
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

import kanban_overlay as app


def task(task_id, text, due=None, status="todo"):
    return {"id": task_id, "text": text, "due": due, "status": status}


class DateTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 9, 21)

    def test_parse_due_shorthand(self):
        self.assertEqual(app.parse_due("today", self.today), self.today)
        self.assertEqual(app.parse_due("tomorrow", self.today), date(2026, 9, 22))
        self.assertEqual(app.parse_due("fri", self.today), date(2026, 9, 25))
        self.assertEqual(app.parse_due("+2w", self.today), date(2026, 10, 5))
        self.assertEqual(app.parse_due("+1m", self.today), date(2026, 10, 21))
        self.assertEqual(app.parse_due("10/3", self.today), date(2026, 10, 3))

    def test_invalid_due_is_kept_in_task_text(self):
        self.assertEqual(app.split_due("Call Alex @frday", self.today),
                         ("Call Alex @frday", None))


class BoardLogicTests(unittest.TestCase):
    def test_busy_month_splits_to_days(self):
        tasks = [task(i, "Task %d" % i, "2026-09-22") for i in range(1, 7)]
        buckets = app.build_buckets(tasks, date(2026, 9, 21), True)
        self.assertEqual([bucket.key for bucket in buckets], ["d2026-09-22"])
        self.assertEqual(buckets[0].open_n, 6)

    def test_completed_tasks_do_not_force_split(self):
        tasks = [task(i, "Open %d" % i, "2026-09-22") for i in range(1, 6)]
        tasks += [task(i, "Done %d" % i, "2026-09-23", "done")
                  for i in range(6, 12)]
        buckets = app.build_buckets(tasks, date(2026, 9, 21), True)
        self.assertEqual([bucket.key for bucket in buckets], ["m2026-09"])

    def test_filters_and_summary_use_the_same_view(self):
        tasks = [
            task(1, "Ship release", "2026-09-20"),
            task(2, "Write release notes", "2026-09-22", "done"),
            task(3, "Buy groceries"),
        ]
        visible = app.apply_filters(tasks, ["release"])
        summary = app.summarise(visible, date(2026, 9, 21))
        self.assertEqual((summary["total"], summary["open"], summary["late"]),
                         (2, 1, 1))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store_patch = patch.multiple(
            app,
            STORE=root / "KanbanOverlay.json",
            BACKUP_STORE=root / "KanbanOverlay.json.bak",
        )
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.temp.cleanup()

    def test_invalid_primary_recovers_and_normalizes_backup(self):
        app.STORE.write_text("{broken", encoding="utf-8")
        app.BACKUP_STORE.write_text(json.dumps({
            "tasks": [
                {"id": "bad", "text": "Recovered", "status": "unknown"},
                {"id": 1, "text": "Second", "status": "doing"},
            ],
            "filters": 42,
        }), encoding="utf-8")

        state = app.load_state()

        self.assertEqual([item["id"] for item in state["tasks"]], [2, 1])
        self.assertEqual(state["tasks"][0]["status"], "todo")
        self.assertEqual(state["filters"], [])

    def test_save_keeps_previous_state_as_backup(self):
        original = dict(app.DEFAULTS)
        original["tasks"] = [task(1, "Original")]
        original["filters"] = []
        app.save_state(original)
        previous = app.STORE.read_text(encoding="utf-8")

        changed = dict(original)
        changed["tasks"] = [task(1, "Changed")]
        app.save_state(changed)

        self.assertEqual(app.BACKUP_STORE.read_text(encoding="utf-8"), previous)


class PackagingTests(unittest.TestCase):
    def test_installed_and_portable_data_directories(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app_dir = root / "app"
            app_dir.mkdir()
            with patch.object(sys, "frozen", True, create=True), \
                    patch.object(app, "APP_DIR", app_dir), \
                    patch.dict(os.environ, {"LOCALAPPDATA": str(root / "local")}):
                self.assertEqual(app.data_dir(), root / "local" / "KanbanOverlay")
                (app_dir / "portable.flag").touch()
                self.assertEqual(app.data_dir(), app_dir)

    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_second_instance_is_rejected(self):
        name = "Local\\KanbanOverlay-test-" + str(uuid.uuid4())
        self.assertTrue(app.acquire_single_instance(name))
        try:
            self.assertFalse(app.acquire_single_instance(name))
        finally:
            app.release_single_instance()


class GeometryTests(unittest.TestCase):
    def test_negative_monitor_coordinates_are_preserved(self):
        self.assertEqual(
            app.clamp_to_area(-1500, 100, 300, 400, (-1920, 0, 0, 1080)),
            (-1500, 100),
        )

    def test_window_is_clamped_to_its_monitor_work_area(self):
        self.assertEqual(
            app.clamp_to_area(-2100, 900, 300, 400, (-1920, 0, 0, 1080)),
            (-1920, 680),
        )

    @unittest.skipUnless(os.name == "nt", "Windows monitor bounds")
    def test_monitor_bounds_include_the_taskbar(self):
        bounds = app.monitor_bounds_at(100, 100)
        work = app.work_area_at(100, 100)
        self.assertIsNotNone(bounds)
        self.assertIsNotNone(work)
        self.assertLessEqual(bounds[0], work[0])
        self.assertLessEqual(bounds[1], work[1])
        self.assertGreaterEqual(bounds[2], work[2])
        self.assertGreaterEqual(bounds[3], work[3])

    @unittest.skipUnless(os.name == "nt", "Windows taskbar")
    def test_native_taskbar_rectangle_is_discovered(self):
        hwnd = app.U.FindWindowW("Shell_TrayWnd", None)
        self.assertTrue(hwnd)
        rect = app.wintypes.RECT()
        self.assertTrue(app.U.GetWindowRect(hwnd, app.ctypes.byref(rect)))
        found = app.taskbar_for_rect(rect.left, rect.top,
                                     rect.right - rect.left,
                                     rect.bottom - rect.top)
        self.assertEqual(int(found), int(hwnd))


class ModuleStateTests(unittest.TestCase):
    """Per-module open/position state, including the pre-module migration."""

    def test_defaults_open_only_the_board(self):
        mods = app._clean_modules({})
        self.assertEqual(sorted(mods), sorted(app.MODULE_KEYS))
        self.assertTrue(mods["board"]["open"])
        self.assertFalse(mods["today"]["open"])
        self.assertTrue(all(m["pos"] is None for m in mods.values()))

    def test_old_panel_open_flag_migrates_to_the_board(self):
        self.assertFalse(app._clean_modules({"panel_open": False})["board"]["open"])
        self.assertTrue(app._clean_modules({"panel_open": True})["board"]["open"])

    def test_explicit_module_state_wins_over_the_legacy_flag(self):
        mods = app._clean_modules({
            "panel_open": False,
            "modules": {"board": {"open": True, "pos": [12, 34]}},
        })
        self.assertTrue(mods["board"]["open"])
        self.assertEqual(mods["board"]["pos"], [12, 34])

    def test_junk_is_discarded_rather_than_crashing_placement(self):
        mods = app._clean_modules({"modules": {
            "board": {"open": True, "pos": ["x", None]},
            "today": {"open": True, "pos": [1, 2, 3]},
            "stats": "not a dict",
            "bogus": {"open": True},
        }})
        self.assertIsNone(mods["board"]["pos"])
        self.assertIsNone(mods["today"]["pos"])
        self.assertIsNone(mods["stats"]["pos"])
        self.assertNotIn("bogus", mods)

    def test_booleans_are_not_mistaken_for_coordinates(self):
        # bool is a subclass of int, so a naive isinstance check lets
        # [True, False] through and the window lands at 1,0
        mods = app._clean_modules({"modules": {"board": {"pos": [True, False]}}})
        self.assertIsNone(mods["board"]["pos"])


    def test_a_board_from_before_the_rename_is_still_read(self):
        """The app used to be called Overlay Board.

        Renaming the store would have looked, to anyone upgrading, exactly
        like every task vanishing.
        """
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "OverlayBoard.json"
            legacy.write_text(json.dumps({
                "tasks": [task(1, "Survived the rename", "2026-09-25")],
            }), encoding="utf-8")
            with patch.object(app, "STORE", Path(tmp) / "KanbanOverlay.json"),                  patch.object(app, "BACKUP_STORE",
                              Path(tmp) / "KanbanOverlay.json.bak"),                  patch.object(app, "LEGACY_STORE", legacy):
                db = app.load_state()
        self.assertEqual([t["text"] for t in db["tasks"]],
                         ["Survived the rename"])


class ReleaseTests(unittest.TestCase):
    """The build reads __version__ out of the source with a regex.

    tools/project.ps1 stamps the EXE, names the installer and validates the
    git tag from that one line, so its exact shape is a build contract, not
    just a string. If this fails, a tagged release would ship an executable
    stamped with the wrong number.
    """

    PS_PATTERN = re.compile(r'(?m)^__version__\s*=\s*"(\d+\.\d+\.\d+)"')

    def test_version_line_is_readable_by_the_build(self):
        source = Path(app.__file__).read_text(encoding="utf-8")
        found = self.PS_PATTERN.findall(source)
        self.assertEqual(len(found), 1,
                         "expected exactly one __version__ line, got %r" % found)
        self.assertEqual(found[0], app.__version__)

    def test_version_is_semver(self):
        self.assertRegex(app.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()