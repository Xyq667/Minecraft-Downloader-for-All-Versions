from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcvd import wiki
from mcvd.net import DownloadError


class WikiSnippetTest(unittest.TestCase):
    def test_clean_snippet_strips_markup_and_entities(self) -> None:
        raw = '挖<span class="searchmatch">钻石</span>矿 &amp; 熔炼'
        self.assertEqual(wiki.clean_snippet(raw), "挖钻石矿 & 熔炼")

    def test_clean_snippet_collapses_spaces(self) -> None:
        self.assertEqual(wiki.clean_snippet("a   b\n\tc"), "a b c")

    def test_html_to_text_keeps_headings_and_paragraphs(self) -> None:
        markup = (
            "<div><h2>获取</h2><p>用铁镐开采。</p>"
            "<ul><li>挖掘</li><li>合成</li></ul></div>"
        )
        text = wiki.html_to_text(markup)
        self.assertIn("== 获取 ==", text)
        self.assertIn("用铁镐开采。", text)
        self.assertIn("挖掘", text)

    def test_html_to_text_drops_scripts_and_styles(self) -> None:
        text = wiki.html_to_text(
            "<style>.x{color:red}</style><script>alert(1)</script><p>正文</p>"
        )
        self.assertEqual(text, "正文")


class WikiUrlTest(unittest.TestCase):
    def test_page_url_encodes_chinese_and_spaces(self) -> None:
        self.assertEqual(
            wiki.page_url("钻石"), "https://zh.minecraft.wiki/w/%E9%92%BB%E7%9F%B3"
        )
        self.assertEqual(
            wiki.page_url("Java 版"), "https://zh.minecraft.wiki/w/Java_%E7%89%88"
        )

    def test_empty_title(self) -> None:
        self.assertEqual(wiki.page_url(""), "https://zh.minecraft.wiki/w/")


class WikiParsingTest(unittest.TestCase):
    SEARCH = {
        "batchcomplete": "",
        "query": {
            "searchinfo": {"totalhits": 2},
            "search": [
                {
                    "ns": 0,
                    "title": "钻石",
                    "pageid": 10487,
                    "wordcount": 411,
                    "snippet": '<span class="searchmatch">钻石</span>是一种稀有物品',
                },
                {"ns": 0, "title": "钻石块", "pageid": 10488, "snippet": ""},
            ],
        },
    }

    def test_parse_search(self) -> None:
        hits = wiki.parse_search(self.SEARCH)
        self.assertEqual([hit.title for hit in hits], ["钻石", "钻石块"])
        self.assertEqual(hits[0].page_id, 10487)
        self.assertEqual(hits[0].word_count, 411)
        self.assertIn("稀有物品", hits[0].snippet)
        self.assertEqual(hits[1].word_count, 0)
        self.assertEqual(hits[0].url, "https://zh.minecraft.wiki/w/%E9%92%BB%E7%9F%B3")

    def test_parse_search_ignores_empty_titles(self) -> None:
        payload = {"query": {"search": [{"title": "", "pageid": 1}]}}
        self.assertEqual(wiki.parse_search(payload), [])

    def test_parse_page(self) -> None:
        payload = {
            "query": {
                "pages": [{"pageid": 7, "title": "红石", "extract": "红石是……"}]
            }
        }
        title, page_id, text = wiki.parse_page(payload)
        self.assertEqual((title, page_id, text), ("红石", 7, "红石是……"))

    def test_parse_page_missing_raises(self) -> None:
        with self.assertRaises(DownloadError):
            wiki.parse_page({"query": {"pages": [{"title": "X", "missing": True}]}})
        with self.assertRaises(DownloadError):
            wiki.parse_page({"query": {"pages": []}})

    def test_search_wiki_uses_extract_api(self) -> None:
        with mock.patch.object(wiki, "_api", return_value=self.SEARCH) as api:
            hits = wiki.search_wiki("钻石", limit=5)
        self.assertEqual(len(hits), 2)
        params = api.call_args[0][0]
        self.assertEqual(params["action"], "query")
        self.assertEqual(params["srsearch"], "钻石")
        self.assertEqual(int(params["srlimit"]), 5)


class WikiCacheTest(unittest.TestCase):
    def test_fetch_writes_and_reuses_cache(self) -> None:
        payload = {
            "query": {
                "pages": [{"pageid": 3, "title": "草方块", "extract": "草方块是……"}]
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "page.json"

            def fake_path(_title: str) -> Path:
                return cache

            rendered = {
                "parse": {
                    "title": "草方块",
                    "pageid": 3,
                    "text": {"*": "<p>草方块是……</p>"},
                }
            }

            def fake_api(params, cancel=None):
                return rendered if params.get("action") == "parse" else payload

            with mock.patch.object(wiki, "_api", side_effect=fake_api) as api:
                with mock.patch.object(wiki, "_cache_path", side_effect=fake_path):
                    first = wiki.fetch_wiki_page("草方块")
                    second = wiki.fetch_wiki_page("草方块")
            self.assertEqual(first.text, "草方块是……")
            self.assertFalse(first.cached)
            self.assertTrue(second.cached)
            self.assertEqual(api.call_count, 1)
            self.assertTrue(cache.is_file())
            self.assertEqual(json.loads(cache.read_text("utf-8"))["title"], "草方块")

    def test_fetch_falls_back_to_rendered_html(self) -> None:
        extract = {
            "query": {"pages": [{"pageid": 4, "title": "泥土", "extract": ""}]}
        }
        rendered = {
            "parse": {
                "title": "泥土",
                "pageid": 4,
                "text": {"*": "<p>泥土是方块。</p>"},
            }
        }

        def fake_api(params, cancel=None):
            if params.get("action") == "parse":
                return rendered
            return extract

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(wiki, "_api", side_effect=fake_api):
                with mock.patch.object(
                    wiki, "_cache_path", return_value=Path(temp) / "x.json"
                ):
                    page = wiki.fetch_wiki_page("泥土", use_cache=False)
        self.assertEqual(page.text, "泥土是方块。")

    def test_fetch_rejects_blank_title(self) -> None:
        with self.assertRaises(DownloadError):
            wiki.fetch_wiki_page("   ")


if __name__ == "__main__":
    unittest.main()
