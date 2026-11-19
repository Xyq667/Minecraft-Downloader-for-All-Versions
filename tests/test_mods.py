from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcvd import mods
from mcvd.net import DownloadError

SEARCH_PAYLOAD = {
    "hits": [
        {
            "project_id": "AANobbMI",
            "slug": "sodium",
            "title": "Sodium",
            "author": "jellysquid3",
            "description": "A high-performance rendering engine",
            "downloads": 254064515,
            "follows": 12000,
            "date_modified": "2026-11-01T10:00:00Z",
            "categories": ["fabric", "optimization"],
            "icon_url": "https://cdn.modrinth.com/x.png",
            "client_side": "required",
            "server_side": "unsupported",
        },
        {"project_id": "", "slug": "", "title": ""},
    ],
    "total_hits": 120,
}

VERSIONS_PAYLOAD = [
    {
        "id": "abc",
        "project_id": "AANobbMI",
        "name": "Sodium 0.5.13",
        "version_number": "mc1.20.1-0.5.13",
        "version_type": "release",
        "game_versions": ["1.20.1"],
        "loaders": ["fabric", "quilt"],
        "date_published": "2025-01-01T00:00:00Z",
        "downloads": 999,
        "files": [
            {
                "url": "https://cdn.modrinth.com/data/x/sodium.jar",
                "filename": "sodium-fabric-0.5.13+mc1.20.1.jar",
                "size": 1234567,
                "primary": True,
                "hashes": {"sha1": "abc123", "sha512": "def456"},
            },
            {
                "url": "https://cdn.modrinth.com/data/x/sodium-sources.jar",
                "filename": "sodium-sources.jar",
                "size": 500,
                "primary": False,
                "hashes": {},
            },
        ],
    },
    {"id": "no-files", "project_id": "AANobbMI", "files": []},
]


class FacetTest(unittest.TestCase):
    def test_default_only_mods(self) -> None:
        self.assertEqual(mods.build_facets(), [["project_type:mod"]])

    def test_game_version_and_loader(self) -> None:
        facets = mods.build_facets("1.20.1", "neoforge")
        self.assertEqual(
            facets,
            [
                ["project_type:mod"],
                ["versions:1.20.1"],
                ["categories:neoforge"],
            ],
        )

    def test_unknown_loader_is_ignored(self) -> None:
        self.assertEqual(
            mods.build_facets(None, "unknown"), [["project_type:mod"]]
        )

    def test_search_url_encodes_facets(self) -> None:
        url = mods.build_search_url("sodium", game_version="1.20.1", loader="fabric")
        self.assertTrue(url.startswith(mods.MODRINTH_API + "/search?"))
        self.assertIn("index=relevance", url)
        self.assertIn("query=sodium", url)
        self.assertIn("project_type%3Amod", url)
        self.assertIn("versions%3A1.20.1", url)


class ParsingTest(unittest.TestCase):
    def test_parse_search(self) -> None:
        projects, total = mods.parse_search(SEARCH_PAYLOAD)
        self.assertEqual(total, 120)
        self.assertEqual(len(projects), 1)
        project = projects[0]
        self.assertEqual(project.title, "Sodium")
        self.assertEqual(project.slug, "sodium")
        self.assertEqual(project.downloads, 254064515)
        self.assertEqual(project.url, "https://modrinth.com/mod/sodium")
        self.assertIn("optimization", project.categories)

    def test_parse_search_rejects_non_dict(self) -> None:
        with self.assertRaises(DownloadError):
            mods.parse_search([])

    def test_parse_versions_uses_primary_file(self) -> None:
        versions = mods.parse_versions(VERSIONS_PAYLOAD)
        self.assertEqual(len(versions), 1)
        version = versions[0]
        self.assertEqual(version.version_number, "mc1.20.1-0.5.13")
        self.assertEqual(version.type_label, "正式版")
        self.assertEqual(version.loader_label, "Fabric, Quilt")
        self.assertEqual(version.file.filename, "sodium-fabric-0.5.13+mc1.20.1.jar")
        self.assertEqual(version.file.sha1, "abc123")
        self.assertEqual(version.file.size, 1234567)

    def test_pick_version_bounds(self) -> None:
        versions = mods.parse_versions(VERSIONS_PAYLOAD)
        self.assertIs(mods.pick_version(versions, 0), versions[0])
        self.assertIs(mods.pick_version(versions, 99), versions[0])
        self.assertIsNone(mods.pick_version([]))


class FilenameTest(unittest.TestCase):
    def test_safe_filename_strips_paths_and_bad_chars(self) -> None:
        self.assertEqual(mods.safe_filename("../../evil.jar"), "evil.jar")
        self.assertEqual(mods.safe_filename('a:b*c?.jar'), "a_b_c_.jar")
        self.assertEqual(mods.safe_filename(""), "mod.jar")

    def test_mods_dir(self) -> None:
        self.assertEqual(mods.mods_dir("D:/Minecraft"), Path("D:/Minecraft/mods"))


class InstallTest(unittest.TestCase):
    def test_install_downloads_into_mods_folder(self) -> None:
        version = mods.parse_versions(VERSIONS_PAYLOAD)[0]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "mods"
            received = []

            def fake_download(item, **kwargs):
                item.dest.parent.mkdir(parents=True, exist_ok=True)
                item.dest.write_bytes(b"jar")
                callback = kwargs.get("chunk_callback")
                if callback is not None:
                    callback(3)
                received.append(kwargs["verify_existing_hash"])
                return 3

            with mock.patch.object(mods, "download_item", side_effect=fake_download):
                install = mods.install_mod(version, target)
            self.assertTrue(install.path.is_file())
            self.assertEqual(install.size, 3)
            self.assertFalse(install.skipped)
            self.assertEqual(received, [True])
            self.assertEqual(install.path.parent, target)

    def test_install_detects_existing_file(self) -> None:
        version = mods.parse_versions(VERSIONS_PAYLOAD)[0]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "mods"
            target.mkdir()
            (target / version.file.filename).write_bytes(b"jar")
            with mock.patch.object(mods, "download_item", return_value=0):
                install = mods.install_mod(version, target)
            self.assertTrue(install.skipped)


if __name__ == "__main__":
    unittest.main()
