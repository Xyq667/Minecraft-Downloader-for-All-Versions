"""Mod download support backed by the public Modrinth API.

Modrinth is used because it needs no API key and exposes a clean v2 REST API:
search (``/search``) and per-project version lists (``/project/{id}/version``).
Downloads are plain jar files verified against the published SHA-1.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from .config import USER_AGENT, format_bytes
from .net import DownloadError, DownloadItem, download_item, fetch_bytes

MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_SITE = "https://modrinth.com"
MODRINTH_TIMEOUT = 30.0

# Loader ids accepted by Modrinth facets and by the /version endpoint.
LOADER_SLUGS = {
    "fabric": "fabric",
    "quilt": "quilt",
    "forge": "forge",
    "neoforge": "neoforge",
}
LOADER_LABELS = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
}
VERSION_TYPE_LABELS = {
    "release": "正式版",
    "beta": "测试版",
    "alpha": "内测版",
}
SORT_OPTIONS = {
    "relevance": "相关度",
    "downloads": "下载量",
    "follows": "关注数",
    "newest": "最新发布",
    "updated": "最近更新",
}

_BAD_FILENAME = re.compile(r"[\\/:*?\"<>|\r\n\t]")


@dataclass(frozen=True)
class ModProject:
    """A mod project from a search result."""

    project_id: str
    slug: str
    title: str
    author: str
    description: str
    downloads: int
    follows: int
    updated: str
    categories: tuple[str, ...]
    icon_url: str
    client_side: str
    server_side: str

    @property
    def url(self) -> str:
        return f"{MODRINTH_SITE}/mod/{self.slug or self.project_id}"


@dataclass(frozen=True)
class ModFile:
    url: str
    filename: str
    size: int
    sha1: str

    @property
    def display_size(self) -> str:
        return format_bytes(self.size)


@dataclass(frozen=True)
class ModVersion:
    """One downloadable file of a mod."""

    version_id: str
    project_id: str
    name: str
    version_number: str
    version_type: str
    game_versions: tuple[str, ...]
    loaders: tuple[str, ...]
    date_published: str
    downloads: int
    file: ModFile

    @property
    def type_label(self) -> str:
        return VERSION_TYPE_LABELS.get(self.version_type, self.version_type or "—")

    @property
    def loader_label(self) -> str:
        return ", ".join(LOADER_LABELS.get(item, item) for item in self.loaders) or "—"

    @property
    def game_label(self) -> str:
        return ", ".join(self.game_versions) or "—"


@dataclass(frozen=True)
class ModInstall:
    path: Path
    size: int
    skipped: bool = False


def build_facets(
    game_version: str | None = None,
    loader: str | None = None,
) -> list[list[str]]:
    """Facets always restrict to mods; version/loader filters are optional."""
    facets: list[list[str]] = [["project_type:mod"]]
    if game_version:
        facets.append([f"versions:{game_version.strip()}"])
    if loader:
        slug = LOADER_SLUGS.get(loader.strip().lower())
        if slug:
            facets.append([f"categories:{slug}"])
    return facets


def build_search_url(
    query: str,
    *,
    game_version: str | None = None,
    loader: str | None = None,
    limit: int = 20,
    offset: int = 0,
    sort: str = "relevance",
) -> str:
    params: dict[str, object] = {
        "query": (query or "").strip(),
        "limit": max(1, min(int(limit), 100)),
        "offset": max(0, int(offset)),
        "index": sort if sort in SORT_OPTIONS else "relevance",
        "facets": json.dumps(build_facets(game_version, loader), separators=(",", ":")),
    }
    return f"{MODRINTH_API}/search?{urlencode(params)}"


def _get_json(url: str, cancel: threading.Event | None = None) -> object:
    data = fetch_bytes(url, timeout=MODRINTH_TIMEOUT, attempts=2, cancel=cancel)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DownloadError(f"Modrinth 返回了无法解析的数据：{exc}") from exc


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_search(payload: object) -> tuple[list[ModProject], int]:
    """Convert a /search payload into projects plus the total hit count."""
    if not isinstance(payload, dict):
        raise DownloadError("Modrinth 返回了异常数据")
    projects: list[ModProject] = []
    for hit in payload.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        project_id = str(hit.get("project_id") or "").strip()
        slug = str(hit.get("slug") or "").strip()
        title = str(hit.get("title") or "").strip()
        if not (project_id or slug) or not title:
            continue
        projects.append(
            ModProject(
                project_id=project_id or slug,
                slug=slug or project_id,
                title=title,
                author=str(hit.get("author") or "—"),
                description=str(hit.get("description") or "").strip(),
                downloads=_as_int(hit.get("downloads")),
                follows=_as_int(hit.get("follows")),
                updated=str(hit.get("date_modified") or ""),
                categories=tuple(str(item) for item in hit.get("categories") or []),
                icon_url=str(hit.get("icon_url") or ""),
                client_side=str(hit.get("client_side") or ""),
                server_side=str(hit.get("server_side") or ""),
            )
        )
    return projects, _as_int(payload.get("total_hits"))


def parse_versions(payload: object) -> list[ModVersion]:
    """Convert a project version list into ModVersion entries (newest first)."""
    if not isinstance(payload, list):
        raise DownloadError("Modrinth 返回了异常数据")
    versions: list[ModVersion] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        files = [entry for entry in item.get("files") or [] if isinstance(entry, dict)]
        if not files:
            continue
        primary = next((entry for entry in files if entry.get("primary")), files[0])
        hashes = primary.get("hashes") or {}
        url = str(primary.get("url") or "").strip()
        filename = str(primary.get("filename") or "").strip()
        if not url or not filename:
            continue
        versions.append(
            ModVersion(
                version_id=str(item.get("id") or ""),
                project_id=str(item.get("project_id") or ""),
                name=str(item.get("name") or "").strip(),
                version_number=str(item.get("version_number") or "").strip(),
                version_type=str(item.get("version_type") or ""),
                game_versions=tuple(str(g) for g in item.get("game_versions") or []),
                loaders=tuple(str(g) for g in item.get("loaders") or []),
                date_published=str(item.get("date_published") or ""),
                downloads=_as_int(item.get("downloads")),
                file=ModFile(
                    url=url,
                    filename=safe_filename(filename),
                    size=_as_int(primary.get("size")),
                    sha1=str(hashes.get("sha1") or ""),
                ),
            )
        )
    return versions


def search_mods(
    query: str,
    *,
    game_version: str | None = None,
    loader: str | None = None,
    limit: int = 20,
    offset: int = 0,
    sort: str = "relevance",
    cancel: threading.Event | None = None,
) -> tuple[list[ModProject], int]:
    """Search Modrinth for mods matching the query and optional filters."""
    url = build_search_url(
        query,
        game_version=game_version,
        loader=loader,
        limit=limit,
        offset=offset,
        sort=sort,
    )
    return parse_search(_get_json(url, cancel))


def fetch_mod_versions(
    project: str,
    *,
    game_version: str | None = None,
    loader: str | None = None,
    cancel: threading.Event | None = None,
) -> list[ModVersion]:
    """List downloadable versions of one project, newest first."""
    key = quote((project or "").strip(), safe="")
    if not key:
        raise DownloadError("模组标识为空")
    params: dict[str, str] = {}
    if game_version:
        params["game_versions"] = json.dumps([game_version.strip()])
    slug = LOADER_SLUGS.get((loader or "").strip().lower())
    if slug:
        params["loaders"] = json.dumps([slug])
    url = f"{MODRINTH_API}/project/{key}/version"
    if params:
        url += "?" + urlencode(params)
    return parse_versions(_get_json(url, cancel))


def pick_version(versions: list[ModVersion], index: int = 0) -> ModVersion | None:
    if not versions:
        return None
    if index < 0 or index >= len(versions):
        index = 0
    return versions[index]


def safe_filename(name: str) -> str:
    """Keep only the last path component and strip characters Windows rejects."""
    cleaned = (name or "").replace("\\", "/").split("/")[-1]
    cleaned = _BAD_FILENAME.sub("_", cleaned).strip().strip(".")
    return cleaned or "mod.jar"


def mods_dir(game_dir: Path | str) -> Path:
    return Path(game_dir) / "mods"


def install_mod(
    version: ModVersion,
    target_dir: Path | str,
    *,
    cancel: threading.Event | None = None,
    on_progress=None,
) -> ModInstall:
    """Download one mod jar into ``target_dir`` (usually ``<game>/mods``)."""
    directory = Path(target_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(f"无法创建模组目录：{exc}") from exc
    dest = directory / version.file.filename
    item = DownloadItem(
        url=version.file.url,
        dest=dest,
        sha1=version.file.sha1 or None,
        size=version.file.size or None,
        label=version.file.filename,
        kind="mod",
    )
    received = download_item(
        item,
        cancel=cancel,
        chunk_callback=on_progress,
        verify_existing_hash=True,
    )
    skipped = received == 0 and dest.is_file()
    try:
        size = dest.stat().st_size
    except OSError:
        size = 0
    return ModInstall(path=dest, size=size, skipped=skipped)
