from __future__ import annotations

import unittest
from unittest import mock

from mcvd import forge
from mcvd.config import Settings
from mcvd.loaders import LoaderChoice


class LoaderMetadataTest(unittest.TestCase):
    def test_java_major_mapping(self) -> None:
        self.assertEqual(forge.java_major_for_mc("1.16.5"), 8)
        self.assertEqual(forge.java_major_for_mc("1.20.1"), 17)
        self.assertEqual(forge.java_major_for_mc("1.20.6"), 21)
        self.assertEqual(forge.java_major_for_mc("26.3"), 25)

    def test_neoforge_mc_mapping(self) -> None:
        self.assertEqual(forge._neoforge_mc("20.4.237"), "1.20.4")
        self.assertEqual(forge._neoforge_mc("21.1.213"), "1.21.1")
        self.assertEqual(forge._neoforge_mc("21.10.5"), "1.21.10")
        self.assertEqual(forge._neoforge_mc("26.1.2.125"), "26.1.2")

    def test_artifact_urls_include_mirrors_and_central(self) -> None:
        urls = forge._artifact_urls(
            "https://maven.neoforged.net/releases/com/google/example/1.0/example-1.0.jar",
            "com/google/example/1.0/example-1.0.jar",
        )
        self.assertIn(
            "https://mirrors.qlu.edu.cn/bmclapi/com/google/example/1.0/example-1.0.jar",
            urls,
        )
        self.assertIn(
            "https://repo1.maven.org/maven2/com/google/example/1.0/example-1.0.jar",
            urls,
        )

    def test_forge_versions_are_parsed_and_sorted(self) -> None:
        xml = (
            "<metadata><versioning><versions>"
            "<version>1.20.1-47.4.10</version>"
            "<version>1.20.1-47.4.28</version>"
            "<version>1.19.2-43.5.0</version>"
            "</versions></versioning></metadata>"
        )
        with mock.patch.object(forge, "_promotions", return_value={}):
            with mock.patch.object(forge, "_metadata_text", return_value=xml):
                releases = forge.list_forge_versions("1.20.1")
        self.assertEqual([item.version for item in releases], ["47.4.28", "47.4.10"])
        self.assertTrue(all(item.java_major == 17 for item in releases))
        self.assertTrue(releases[0].installer_urls[0].endswith("-installer.jar"))

    def test_neoforge_versions_filter_mc(self) -> None:
        xml = (
            "<metadata><versioning><versions>"
            "<version>20.4.237</version>"
            "<version>21.1.213</version>"
            "<version>21.1.214</version>"
            "<version>21.1.215-beta</version>"
            "</versions></versioning></metadata>"
        )
        with mock.patch.object(forge, "_metadata_text", return_value=xml):
            releases = forge.list_neoforge_versions("1.21.1")
        self.assertEqual([item.version for item in releases], ["21.1.214", "21.1.213"])
        self.assertTrue(all(item.kind == "neoforge" for item in releases))

    def test_loader_label_and_settings_validation(self) -> None:
        stable = LoaderChoice("forge", "1.20.1", "47.4.28", True)
        test = LoaderChoice("neoforge", "1.21.1", "21.1.300-beta", False)
        self.assertEqual(stable.label, "47.4.28")
        self.assertEqual(test.label, "21.1.300-beta 测试")
        self.assertEqual(Settings(loader="bad").loader, "none")
        self.assertEqual(Settings(loader="forge").loader, "forge")


if __name__ == "__main__":
    unittest.main()
