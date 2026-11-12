from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcvd.config import format_clock
from mcvd.stats import average_speed, clear_stats, load_stats, record_download


def make_result(**overrides):
    values = {
        "elapsed": 12.5,
        "session_bytes": 2048,
        "installed": 2,
        "skipped": 1,
        "failed": 0,
        "cancelled": False,
        "requested": 3,
        "version_ids": ["1.20.1", "1.21"],
        "version_elapsed": {"1.20.1": 5.0, "1.21": 7.0},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DownloadStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "download_stats.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_record_accumulates_time_and_history(self) -> None:
        first = record_download(
            make_result(), Path("D:/Minecraft"), "full", path=self.path
        )
        self.assertEqual(first["total_runs"], 1)
        self.assertEqual(first["total_seconds"], 12.5)
        self.assertEqual(first["total_bytes"], 2048)
        self.assertEqual(first["total_installed"], 2)
        self.assertEqual(first["total_skipped"], 1)
        self.assertEqual(first["recent"][0]["versions"], ["1.20.1", "1.21"])
        self.assertAlmostEqual(first["recent"][0]["average_speed"], 2048 / 12.5)
        self.assertEqual(first["versions"]["1.20.1"]["seconds"], 5.0)

        second = record_download(
            make_result(
                elapsed=7.5,
                session_bytes=1024,
                installed=1,
                skipped=0,
                failed=1,
                cancelled=True,
                requested=1,
                version_ids=["1.21.5"],
                version_elapsed={"1.21.5": 7.5},
            ),
            Path("D:/Minecraft"),
            "core",
            path=self.path,
        )
        self.assertEqual(second["total_runs"], 2)
        self.assertEqual(second["total_seconds"], 20.0)
        self.assertEqual(second["total_bytes"], 3072)
        self.assertEqual(second["total_cancelled"], 1)
        self.assertEqual(second["recent"][0]["target"], "D:\\Minecraft")
        self.assertEqual(second["recent"][0]["mode"], "core")
        self.assertEqual(len(second["recent"]), 2)
        self.assertAlmostEqual(average_speed(second), 153.6)
        self.assertEqual(second["versions"]["1.21.5"]["runs"], 1)

        reloaded = load_stats(self.path)
        self.assertEqual(reloaded["total_seconds"], 20.0)
        self.assertEqual(reloaded["recent"][1]["versions"], ["1.20.1", "1.21"])

    def test_clock_format_for_timer(self) -> None:
        self.assertEqual(format_clock(0), "00:00")
        self.assertEqual(format_clock(65), "01:05")
        self.assertEqual(format_clock(3661), "01:01:01")
        self.assertEqual(format_clock(-1), "--:--")

    def test_bad_file_is_ignored(self) -> None:
        self.path.write_text("{not-json", encoding="utf-8")
        data = load_stats(self.path)
        self.assertEqual(data["total_runs"], 0)
        self.assertEqual(data["recent"], [])
        self.assertEqual(data["total_seconds"], 0.0)

    def test_recent_history_is_capped(self) -> None:
        for _ in range(45):
            record_download(make_result(), Path("D:/Minecraft"), "core", path=self.path)
        data = load_stats(self.path)
        self.assertEqual(len(data["recent"]), 40)
        self.assertEqual(data["total_runs"], 45)

    def test_clear_stats_keeps_downloaded_files_untouched(self) -> None:
        record_download(make_result(), Path("D:/Minecraft"), "full", path=self.path)
        self.assertTrue(self.path.is_file())
        clear_stats(self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(load_stats(self.path)["total_runs"], 0)

    def test_normalizes_hand_edited_values(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "total_runs": "3",
                    "total_seconds": -2,
                    "total_bytes": 10,
                    "recent": [
                        {
                            "at": "2026-01-01T00:00:00+08:00",
                            "elapsed": "invalid",
                            "bytes": 10,
                            "versions": ["1.20.1", 42],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        data = load_stats(self.path)
        self.assertEqual(data["total_runs"], 3)
        self.assertEqual(data["total_seconds"], 0.0)
        self.assertEqual(data["recent"][0]["versions"], ["1.20.1"])
        self.assertEqual(data["recent"][0]["bytes"], 10)


if __name__ == "__main__":
    unittest.main()
