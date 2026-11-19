"""Minecraft Wiki (zh.minecraft.wiki) client used by the Wiki window.

The wiki is a MediaWiki site, so everything goes through ``api.php``:

* search  -> ``action=query&list=search``
* reading -> ``action=query&prop=extracts&explaintext=1``

Plain-text extracts keep the in-app reader simple (no HTML rendering in Tk),
while ``page_url()`` builds the canonical link for "open in browser".
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import re
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode

from .config import cache_dir
from .net import DownloadError, fetch_bytes

WIKI_BASE = "https://zh.minecraft.wiki"
WIKI_API = f"{WIKI_BASE}/api.php"
WIKI_SITE_NAME = "Minecraft Wiki（中文）"
WIKI_TIMEOUT = 30.0
CACHE_TTL = 7 * 24 * 3600

_WHITESPACE = re.compile(r"\s+")
_NEWLINES = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class WikiHit:
    """One search result row."""

    title: str
    page_id: int
    snippet: str
    word_count: int = 0

    @property
    def url(self) -> str:
        return page_url(self.title)


@dataclass(frozen=True)
class WikiPage:
    """A rendered wiki article as plain text."""

    title: str
    page_id: int
    text: str
    cached: bool = False

    @property
    def url(self) -> str:
        return page_url(self.title)

    def summary(self, limit: int = 160) -> str:
        for line in self.text.splitlines():
            line = line.strip()
            if line and not line.startswith("=="):
                return line[:limit]
        return ""


def page_url(title: str) -> str:
    """Canonical article URL (spaces become underscores, UTF-8 percent encoded)."""
    slug = (title or "").strip().replace(" ", "_")
    return f"{WIKI_BASE}/w/{quote(slug, safe='')}"


def clean_snippet(value: str) -> str:
    """Turn the HTML-ish search snippet into one readable line."""
    text = re.sub(r"<[^>]+>", "", value or "")
    text = html_module.unescape(text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text


class _TextExtractor(HTMLParser):
    """Small HTML -> text converter that keeps article prose and tables.

    Wikipedia-style pages wrap a lot of chrome in ``div``/``table`` elements
    with telling class names (infobox, navbox, hatnote, references...). Those
    subtrees are skipped so the reader shows the article itself.
    """

    BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5",
        "h6", "section", "blockquote", "dt", "dd", "ul", "ol", "figure",
    }
    SKIP_TAGS = {"style", "script", "noscript", "sup"}
    SKIP_CLASSES = {
        "navbox", "infobox", "hatnote", "noprint", "metadata", "mw-editsection",
        "reference", "reflist", "sidebar", "toc", "messagebox", "ambox", "mbox",
        "mw-references-wrap", "printfooter", "catlinks", "navbox-styles",
        "mw-empty-elt", "mw-hidden-catlinks", "shortdescription",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._stack: list[str] = []
        self._skip_from: int | None = None
        self._heading: str | None = None

    def _should_skip(self, tag: str, attrs) -> bool:
        if tag in self.SKIP_TAGS:
            return True
        classes = ""
        for name, value in attrs:
            if name == "class" and value:
                classes = value.lower()
                break
        if not classes:
            return False
        return any(token in classes for token in self.SKIP_CLASSES)

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._skip_from is not None:
            if tag not in ("br", "img", "meta", "link", "input", "hr"):
                self._stack.append(tag)
            return
        if self._should_skip(tag, attrs):
            self._skip_from = len(self._stack)
            if tag not in ("br", "img", "meta", "link", "input", "hr"):
                self._stack.append(tag)
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "td" or tag == "th":
            self.parts.append(" ")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = tag
        if tag not in ("br", "img", "meta", "link", "input", "hr"):
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in ("br", "img", "meta", "link", "input", "hr"):
            if self._stack and self._stack[-1] == tag:
                self._stack.pop()
            elif tag in self._stack:
                while self._stack and self._stack[-1] != tag:
                    self._stack.pop()
                if self._stack:
                    self._stack.pop()
        if self._skip_from is not None:
            if len(self._stack) <= self._skip_from:
                self._skip_from = None
            return
        if tag in ("td", "th"):
            self.parts.append(" | ")
        if tag == "tr":
            self.parts.append("\n")
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == self._heading:
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._skip_from is not None:
            return
        text = _WHITESPACE.sub(" ", data)
        if not text.strip():
            return
        if self._heading:
            level = int(self._heading[1]) if self._heading[1].isdigit() else 2
            marker = "=" * max(2, min(level, 4))
            self.parts.append(f"\n{marker} {text.strip()} {marker}\n")
            return
        self.parts.append(text)


def html_to_text(markup: str) -> str:
    """Convert article HTML to plain text with readable paragraph breaks."""
    parser = _TextExtractor()
    try:
        parser.feed(markup or "")
        parser.close()
    except Exception:
        return clean_snippet(markup)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"(?: \| \n)+", "\n", text)
    text = _NEWLINES.sub("\n\n", text)
    return text.strip()


def _api(params: dict[str, object], cancel: threading.Event | None = None) -> dict:
    query = {
        "format": "json",
        "formatversion": "2",
        "utf8": "1",
    }
    query.update(params)
    url = f"{WIKI_API}?{urlencode(query)}"
    data = fetch_bytes(url, timeout=WIKI_TIMEOUT, attempts=2, cancel=cancel)
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DownloadError(f"Wiki 返回了无法解析的数据：{exc}") from exc
    if not isinstance(payload, dict):
        raise DownloadError("Wiki 返回了异常数据")
    if "error" in payload:
        info = payload["error"].get("info") or payload["error"].get("code") or "未知错误"
        raise DownloadError(f"Wiki 接口错误：{info}")
    return payload


def parse_search(payload: dict) -> list[WikiHit]:
    """Convert a ``list=search`` payload into hits (kept pure for tests)."""
    hits: list[WikiHit] = []
    for item in (payload.get("query") or {}).get("search") or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        try:
            page_id = int(item.get("pageid") or 0)
        except (TypeError, ValueError):
            page_id = 0
        try:
            words = int(item.get("wordcount") or 0)
        except (TypeError, ValueError):
            words = 0
        hits.append(
            WikiHit(
                title=title,
                page_id=page_id,
                snippet=clean_snippet(str(item.get("snippet") or "")),
                word_count=words,
            )
        )
    return hits


def search_wiki(
    query: str,
    *,
    limit: int = 20,
    cancel: threading.Event | None = None,
) -> list[WikiHit]:
    """Full-text search on the Chinese Minecraft Wiki."""
    text = (query or "").strip()
    if not text:
        return []
    payload = _api(
        {
            "action": "query",
            "list": "search",
            "srsearch": text,
            "srnamespace": "0",
            "srlimit": max(1, min(int(limit), 50)),
            "srprop": "snippet|wordcount",
        },
        cancel,
    )
    return parse_search(payload)


def parse_page(payload: dict) -> tuple[str, int, str]:
    """Extract (title, page_id, text) from a ``prop=extracts`` payload."""
    pages = (payload.get("query") or {}).get("pages") or []
    if isinstance(pages, dict):  # formatversion=1 compatibility
        pages = list(pages.values())
    if not pages:
        raise DownloadError("找不到该条目")
    page = pages[0]
    if page.get("missing"):
        raise DownloadError("找不到该条目，可能已被重命名")
    title = str(page.get("title") or "").strip()
    try:
        page_id = int(page.get("pageid") or 0)
    except (TypeError, ValueError):
        page_id = 0
    text = str(page.get("extract") or "").strip()
    return title, page_id, text


def _cache_path(title: str) -> Path:
    digest = hashlib.sha1(title.strip().lower().encode("utf-8")).hexdigest()
    return cache_dir() / "wiki" / f"{digest}.json"


def _read_cache(title: str) -> WikiPage | None:
    path = _cache_path(title)
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    text = str(payload.get("text") or "")
    if not text:
        return None
    return WikiPage(
        title=str(payload.get("title") or title),
        page_id=int(payload.get("page_id") or 0),
        text=text,
        cached=True,
    )


def _write_cache(page: WikiPage) -> None:
    path = _cache_path(page.title)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"title": page.title, "page_id": page.page_id, "text": page.text},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _fetch_extract(title: str, cancel: threading.Event | None) -> tuple[str, int, str]:
    payload = _api(
        {
            "action": "query",
            "prop": "extracts",
            "explaintext": "1",
            "titles": title,
            "redirects": "1",
            "exlimit": "1",
        },
        cancel,
    )
    return parse_page(payload)


def _fetch_rendered(title: str, cancel: threading.Event | None) -> tuple[str, int, str]:
    payload = _api(
        {
            "action": "parse",
            "page": title,
            "prop": "text|displaytitle",
            "disableeditsection": "1",
            "disabletoc": "1",
            "redirects": "1",
        },
        cancel,
    )
    parsed = payload.get("parse") or {}
    if not parsed:
        raise DownloadError("找不到该条目")
    raw = parsed.get("text") or ""
    if isinstance(raw, dict):
        raw = raw.get("*", "")
    text = html_to_text(str(raw))
    try:
        page_id = int(parsed.get("pageid") or 0)
    except (TypeError, ValueError):
        page_id = 0
    return str(parsed.get("title") or title), page_id, text


def fetch_wiki_page(
    title: str,
    *,
    cancel: threading.Event | None = None,
    use_cache: bool = True,
) -> WikiPage:
    """Return the article text.

    The rendered HTML is preferred because plain-text extracts drop tables and
    templates (crafting grids, loot tables), which carry a lot of the actual
    article content. Extracts are the fallback when rendering fails.
    """
    name = (title or "").strip()
    if not name:
        raise DownloadError("条目名称为空")
    if use_cache:
        cached = _read_cache(name)
        if cached is not None:
            return cached
    page_title, page_id, text = name, 0, ""
    try:
        page_title, page_id, text = _fetch_rendered(name, cancel)
    except DownloadCancelled:
        raise
    except Exception:
        text = ""
    if not text.strip():
        page_title, page_id, text = _fetch_extract(name, cancel)
    if not text.strip():
        raise DownloadError("该条目没有可显示的内容")
    page = WikiPage(title=page_title or name, page_id=page_id, text=text)
    _write_cache(page)
    return page


def random_page(cancel: threading.Event | None = None) -> str:
    """Pick a random main-namespace article title."""
    payload = _api(
        {
            "action": "query",
            "list": "random",
            "rnnamespace": "0",
            "rnlimit": "1",
        },
        cancel,
    )
    items = (payload.get("query") or {}).get("random") or []
    if items and isinstance(items[0], dict):
        title = str(items[0].get("title") or "").strip()
        if title:
            return title
    raise DownloadError("没有取到随机条目")
