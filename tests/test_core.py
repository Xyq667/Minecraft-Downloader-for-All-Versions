from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from mcvd import core, detect
from mcvd.config import normalize_game_dir
from mcvd.core import DownloadEngine, VersionEntry, rules_allow


def digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        pass


class DownloaderIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.web = self.root / "web"
        self.web.mkdir()
        handler = partial(QuietHandler, directory=str(self.web))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.game = self.root / "game"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def write(self, relative: str, data: bytes) -> dict:
        path = self.web / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {
            "url": f"{self.base}/{relative}",
            "sha1": digest(data),
            "size": len(data),
            "path": relative,
        }

    def build_version(self, version_id: str = "test-1.0", legacy: bool = True) -> VersionEntry:
        client_data = b"fake client jar" * 1000
        client = self.write(f"objects/{version_id}/client.jar", client_data)
        lib_data = b"fake library"
        lib = self.write("libraries/example/lib/1.0/lib-1.0.jar", lib_data)
        lib["path"] = "example/lib/1.0/lib-1.0.jar"
        native_data = b"fake native"
        native = self.write(
            "libraries/example/native/1.0/native-1.0-natives-windows.jar", native_data
        )
        native["path"] = "example/native/1.0/native-1.0-natives-windows.jar"
        log_data = b"<log/>"
        log = self.write("log.xml", log_data)
        asset_data = b"hello asset"
        asset_hash = digest(asset_data)
        (self.web / "assets" / asset_hash[:2]).mkdir(parents=True, exist_ok=True)
        (self.web / "assets" / asset_hash[:2] / asset_hash).write_bytes(asset_data)
        index_payload = {
            "objects": {
                "sound/test.ogg": {"hash": asset_hash, "size": len(asset_data)}
            }
        }
        if legacy:
            index_payload["map_to_resources"] = True
        index_data = json.dumps(index_payload).encode()
        index = self.write("indexes/test.json", index_data)
        version_payload = {
            "id": version_id,
            "type": "release",
            "downloads": {"client": client},
            "libraries": [
                {
                    "name": "example:lib:1.0",
                    "downloads": {"artifact": lib},
                },
                {
                    "name": "example:native:1.0",
                    "downloads": {
                        "classifiers": {
                            "natives-windows": {**native, "path": native["path"]}
                        }
                    },
                    "natives": {"windows": "natives-windows"},
                },
                {
                    "name": "example:mac-only:1.0",
                    "rules": [{"action": "allow", "os": {"name": "osx"}}],
                    "downloads": {"artifact": {**lib, "path": "mac-only.jar"}},
                },
            ],
            "assetIndex": {
                "id": "test",
                "url": index["url"],
                "sha1": index["sha1"],
                "size": index["size"],
            },
            "logging": {
                "client": {
                    "file": {
                        "id": "client-test.xml",
                        "url": log["url"],
                        "sha1": log["sha1"],
                        "size": log["size"],
                    }
                }
            },
        }
        version_data = json.dumps(version_payload).encode()
        version_file = self.write(f"versions/{version_id}.json", version_data)
        return VersionEntry(
            id=version_id,
            type="release",
            url=version_file["url"],
            sha1=version_file["sha1"],
            release_time="2020-01-01T00:00:00+00:00",
            time="2020-01-01T00:00:00+00:00",
        )

    def test_full_install_and_skip(self) -> None:
        entry = self.build_version()
        snapshots = []
        with mock.patch.object(core, "ASSET_BASE", f"{self.base}/assets"):
            engine = DownloadEngine(
                self.game,
                "full",
                skip_existing=True,
                workers=4,
                on_progress=snapshots.append,
            )
            result = engine.run([entry])
        self.assertEqual(result.installed, 1, result.failures)
        self.assertEqual(result.failed, 0)
        version_dir = self.game / "versions" / entry.id
        self.assertTrue((version_dir / f"{entry.id}.json").is_file())
        self.assertTrue((version_dir / f"{entry.id}.jar").is_file())
        self.assertTrue((version_dir / ".mcvdl_full.json").is_file())
        self.assertTrue((self.game / "libraries" / "example" / "lib" / "1.0" / "lib-1.0.jar").is_file())
        self.assertTrue(
            (
                self.game
                / "libraries"
                / "example"
                / "native"
                / "1.0"
                / "native-1.0-natives-windows.jar"
            ).is_file()
        )
        self.assertFalse(
            (self.game / "libraries" / "example" / "mac-only" / "1.0" / "mac-only.jar").exists()
        )
        self.assertTrue((self.game / "assets" / "log_configs" / "client-test.xml").is_file())
        self.assertTrue((self.game / "resources" / "sound" / "test.ogg").is_file())
        self.assertGreater(len(snapshots), 0)
        self.assertEqual(snapshots[-1].overall, 1.0)

        second = DownloadEngine(self.game, "full", skip_existing=True).run([entry])
        self.assertEqual(second.skipped, 1)
        self.assertEqual(second.installed, 0)

    def test_version_folder_created_before_skip_check(self) -> None:
        entry = self.build_version("folder-test")
        engine = DownloadEngine(self.game, "core", skip_existing=True, workers=2)
        self.assertTrue(engine._ensure_version_dir(entry))
        self.assertTrue((self.game / "versions" / entry.id).is_dir())
        # An empty pre-created folder must not be treated as installed.
        self.assertFalse(engine._already_complete(entry))
        result = engine.run([entry])
        self.assertEqual(result.installed, 1, result.failures)
        self.assertEqual(result.skipped, 0)

    def test_core_install(self) -> None:
        entry = self.build_version("core-test")
        engine = DownloadEngine(self.game, "core", skip_existing=True, workers=2)
        result = engine.run([entry])
        self.assertEqual(result.installed, 1, result.failures)
        jar = self.game / "versions" / entry.id / f"{entry.id}.jar"
        self.assertTrue(jar.is_file())
        self.assertTrue((self.game / "versions" / entry.id / f"{entry.id}.json").is_file())
        self.assertIn(jar, result.created_files)
        self.assertEqual(result.version_ids, [entry.id])
        self.assertGreater(result.version_elapsed.get(entry.id, 0.0), 0.0)
        self.assertFalse((self.game / "libraries").exists())
        self.assertFalse((self.game / "assets").exists())

    def test_delete_created_files(self) -> None:
        entry = self.build_version("delete-test")
        engine = DownloadEngine(self.game, "core", skip_existing=True, workers=2)
        result = engine.run([entry])
        self.assertEqual(result.installed, 1, result.failures)
        removed, failed = core.delete_created_files(self.game, result.created_files)
        self.assertEqual(failed, 0)
        self.assertGreaterEqual(removed, 2)
        self.assertFalse((self.game / "versions" / entry.id).exists())

    def test_cancel(self) -> None:
        entry = self.build_version("cancel-test")
        cancel = threading.Event()
        cancel.set()
        engine = DownloadEngine(self.game, "full", cancel=cancel)
        result = engine.run([entry])
        self.assertTrue(result.cancelled)
        self.assertEqual(result.installed, 0)

    def test_rules(self) -> None:
        self.assertTrue(rules_allow(None))
        self.assertTrue(rules_allow([{"action": "allow"}]))
        self.assertFalse(rules_allow([{"action": "allow", "os": {"name": "osx"}}]))
        self.assertTrue(
            rules_allow(
                [
                    {"action": "allow"},
                    {"action": "disallow", "os": {"name": "osx"}},
                ]
            )
        )
        self.assertFalse(
            rules_allow(
                [
                    {"action": "allow"},
                    {"action": "disallow", "os": {"name": "windows"}},
                ]
            )
        )

    def test_safe_join(self) -> None:
        base = self.root / "safe"
        self.assertTrue(str(core.safe_join(base, "a/b.txt")).endswith("a\\b.txt"))
        with self.assertRaises(ValueError):
            core.safe_join(base, "../../escape.txt")


class DetectionTest(unittest.TestCase):
    def test_detect_synthetic_launchers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official = root / "AppData" / "Roaming" / ".minecraft"
            (official / "versions" / "1.20.1").mkdir(parents=True)
            (official / "versions" / "1.20.1" / "1.20.1.json").write_text("{}")
            (official / "versions" / "1.20.1" / "1.20.1.jar").write_bytes(b"x")
            hmcl = root / "HMCL" / ".minecraft"
            (hmcl / "versions" / "1.12.2").mkdir(parents=True)
            prism = root / "AppData" / "Roaming" / "PrismLauncher" / "instances" / "Pack" / "minecraft"
            (prism / "versions" / "1.19.2").mkdir(parents=True)
            with mock.patch.object(detect, "_standard_candidates", return_value=[official, hmcl]):
                with mock.patch.object(detect, "_instance_roots", return_value=[prism.parent]):
                    with mock.patch.object(detect, "_official_profile_dirs", return_value=[]):
                        with mock.patch.object(detect, "_drive_roots", return_value=[]):
                            results = detect.detect_launchers(deep=False, timeout=2)
            paths = {item.path for item in results}
            self.assertIn(official.resolve(), paths)
            self.assertIn(hmcl.resolve(), paths)
            self.assertIn(prism.resolve(), paths)
            official_item = next(item for item in results if item.path == official.resolve())
            self.assertEqual(official_item.version_count, 1)
            prism_item = next(item for item in results if item.path == prism.resolve())
            self.assertIn("Prism", prism_item.label)

    def test_normalize_versions_path(self) -> None:
        path = normalize_game_dir(str(Path(tempfile.gettempdir()) / "Game" / "versions"))
        self.assertEqual(path.name, "Game")


if __name__ == "__main__":
    unittest.main()
