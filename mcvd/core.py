"""Minecraft official manifest handling and a resumable, checksummed downloader."""

from __future__ import annotations

import json
import os
import platform
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import net
from .config import (
    APP_VERSION,
    MANIFEST_URLS,
    TYPE_LABELS,
    cache_dir,
    format_bytes,
    resource_path,
    safe_version_id,
    truncate_middle,
)
from .net import DownloadCancelled, DownloadError, DownloadItem

ASSET_BASE = "https://resources.download.minecraft.net"
DEFAULT_LIBRARY_BASE = "https://libraries.minecraft.net/"


@dataclass(frozen=True)
class VersionEntry:
    id: str
    type: str
    url: str
    sha1: str | None
    release_time: str
    time: str

    @property
    def type_label(self) -> str:
        return TYPE_LABELS.get(self.type, self.type)


@dataclass
class Manifest:
    versions: list[VersionEntry]
    latest_release: str | None
    latest_snapshot: str | None
    source: str
    fetched_at: float
    warning: str = ""

    @property
    def offline(self) -> bool:
        return self.source != "network"

    @property
    def source_label(self) -> str:
        return {
            "network": "官方在线",
            "cache": "本地缓存",
            "snapshot": "内置快照",
        }.get(self.source, self.source)


def _manifest_from_payload(payload: Any) -> Manifest:
    if not isinstance(payload, dict) or not isinstance(payload.get("versions"), list):
        raise ValueError("版本清单格式不正确")
    versions: list[VersionEntry] = []
    for raw in payload["versions"]:
        if not isinstance(raw, dict):
            continue
        version_id = str(raw.get("id", "")).strip()
        url = str(raw.get("url", "")).strip()
        if not version_id or not url:
            continue
        try:
            safe_version_id(version_id)
        except ValueError:
            continue
        versions.append(
            VersionEntry(
                id=version_id,
                type=str(raw.get("type", "release")),
                url=url,
                sha1=raw.get("sha1"),
                release_time=str(raw.get("releaseTime") or raw.get("time") or ""),
                time=str(raw.get("time") or ""),
            )
        )
    if not versions:
        raise ValueError("版本清单中没有可用版本")
    latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}
    return Manifest(
        versions=versions,
        latest_release=latest.get("release"),
        latest_snapshot=latest.get("snapshot"),
        source="network",
        fetched_at=time.time(),
    )


def _cache_manifest_bytes(data: bytes) -> None:
    path = cache_dir() / "version_manifest_v2.json"
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        pass


def _load_local_manifest(path: Path, source: str, warning: str = "") -> Manifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = _manifest_from_payload(payload)
        manifest.source = source
        manifest.fetched_at = path.stat().st_mtime
        manifest.warning = warning
        return manifest
    except (OSError, ValueError, UnicodeError) as exc:
        raise DownloadError(f"无法读取本地版本清单：{exc}") from exc


def fetch_manifest(cancel: threading.Event | None = None) -> Manifest:
    """Fetch the live manifest, falling back to local cache then bundled snapshot."""
    errors: list[str] = []
    for url in MANIFEST_URLS:
        try:
            data = net.fetch_bytes(url, cancel=cancel, attempts=2)
            manifest = _manifest_from_payload(json.loads(data.decode("utf-8")))
            manifest.fetched_at = time.time()
            _cache_manifest_bytes(data)
            return manifest
        except DownloadCancelled:
            raise
        except Exception as exc:
            errors.append(str(exc))
    warning = "无法连接 Mojang，已使用本地版本清单"
    cached = cache_dir() / "version_manifest_v2.json"
    if cached.is_file():
        manifest = _load_local_manifest(cached, "cache", warning)
        manifest.warning = warning
        return manifest
    snapshot = resource_path("assets/version_manifest_snapshot.json")
    if snapshot.is_file():
        manifest = _load_local_manifest(snapshot, "snapshot", warning + "（内置快照）")
        return manifest
    detail = errors[0] if errors else "未知错误"
    raise DownloadError(f"无法获取版本清单：{detail}")


# ---------------------------------------------------------------------------
# Manifest / version JSON inspection helpers


def get_latest_badge(version: VersionEntry, manifest: Manifest | None) -> str:
    if manifest and version.id == manifest.latest_release:
        return " · 最新"
    return ""


def _rule_os_matches(rule_os: dict[str, Any]) -> bool:
    if not rule_os:
        return True
    name = rule_os.get("name")
    if name and name != "windows":
        return False
    arch = str(rule_os.get("arch") or "").lower()
    if arch:
        is_64 = sys.maxsize > 2**32
        if arch in ("x86", "i386", "i686") and is_64:
            return False
        if arch in ("x86_64", "amd64", "arm64") and not is_64:
            return False
    version_pattern = rule_os.get("version")
    if version_pattern:
        try:
            if re.search(version_pattern, platform.version()) is None:
                return False
        except re.error:
            return False
    return True


def rules_allow(rules: Any) -> bool:
    """Evaluate Mojang's allow/disallow rules for a normal Windows client."""
    if not rules:
        return True
    if not isinstance(rules, list):
        return True
    allowed = False
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        features = rule.get("features")
        if isinstance(features, dict):
            # We never launch with demo/custom-resolution/quick-play features.
            if any(bool(value) for value in features.values()):
                continue
        rule_os = rule.get("os")
        if rule_os is not None and not _rule_os_matches(rule_os):
            continue
        allowed = rule.get("action") == "allow"
    return allowed


def maven_path_from_name(name: str) -> str:
    """Convert ``group:artifact:version[:classifier]`` to a maven relative path."""
    parts = name.split(":")
    if len(parts) < 3:
        return ""
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    filename = f"{artifact}-{version}"
    if classifier:
        filename += f"-{classifier}"
    filename += ".jar"
    return "/".join(group.split(".")) + f"/{artifact}/{version}/{filename}"


def _library_item(
    raw: dict[str, Any], libraries_dir: Path, seen: set[str]
) -> list[DownloadItem]:
    if not rules_allow(raw.get("rules")):
        return []
    name = str(raw.get("name") or "")
    downloads = raw.get("downloads") if isinstance(raw.get("downloads"), dict) else {}
    items: list[DownloadItem] = []

    artifact = downloads.get("artifact") if isinstance(downloads, dict) else None
    if isinstance(artifact, dict) and artifact.get("path") and artifact.get("url"):
        path = str(artifact["path"])
        dest = safe_join(libraries_dir, path)
        key = str(dest).casefold()
        if key not in seen:
            seen.add(key)
            items.append(
                DownloadItem(
                    url=str(artifact["url"]),
                    dest=dest,
                    sha1=artifact.get("sha1"),
                    size=_as_int(artifact.get("size")),
                    label=f"库 · {name or path}",
                    kind="library",
                )
            )
    elif not (isinstance(raw.get("natives"), dict) and raw.get("natives", {}).get("windows")):
        # A few very old manifests omit the downloads block entirely. Only
        # synthesize a maven URL when this is a regular library, not when the
        # entry exists solely to provide a Windows native classifier.
        fallback = maven_path_from_name(name)
        base = str(raw.get("url") or DEFAULT_LIBRARY_BASE)
        if fallback and base:
            dest = safe_join(libraries_dir, fallback)
            key = str(dest).casefold()
            if key not in seen:
                seen.add(key)
                items.append(
                    DownloadItem(
                        url=base.rstrip("/") + "/" + fallback,
                        dest=dest,
                        sha1=raw.get("sha1"),
                        size=_as_int(raw.get("size")),
                        label=f"库 · {name}",
                        kind="library",
                    )
                )

    natives = raw.get("natives")
    if isinstance(natives, dict):
        classifier_template = natives.get("windows")
        if classifier_template:
            classifier = str(classifier_template).replace(
                "${arch}", "64" if sys.maxsize > 2**32 else "32"
            )
            classifiers = downloads.get("classifiers") if isinstance(downloads, dict) else None
            native = classifiers.get(classifier) if isinstance(classifiers, dict) else None
            if isinstance(native, dict) and native.get("path") and native.get("url"):
                dest = safe_join(libraries_dir, str(native["path"]))
                key = str(dest).casefold()
                if key not in seen:
                    seen.add(key)
                    items.append(
                        DownloadItem(
                            url=str(native["url"]),
                            dest=dest,
                            sha1=native.get("sha1"),
                            size=_as_int(native.get("size")),
                            label=f"本地库 · {name}",
                            kind="native",
                        )
                    )
            elif name:
                # Very old manifests occasionally lack a downloads block. Build
                # the classifier path from the maven coordinate as a fallback.
                fallback = maven_path_from_name(name + ":" + classifier)
                base = str(raw.get("url") or DEFAULT_LIBRARY_BASE)
                if fallback:
                    dest = safe_join(libraries_dir, fallback)
                    key = str(dest).casefold()
                    if key not in seen:
                        seen.add(key)
                        items.append(
                            DownloadItem(
                                url=base.rstrip("/") + "/" + fallback,
                                dest=dest,
                                label=f"本地库 · {name}",
                                kind="native",
                            )
                        )
    return items


def collect_library_items(data: dict[str, Any], game_dir: Path) -> list[DownloadItem]:
    libraries_dir = game_dir / "libraries"
    seen: set[str] = set()
    items: list[DownloadItem] = []
    for raw in data.get("libraries") or []:
        if isinstance(raw, dict):
            items.extend(_library_item(raw, libraries_dir, seen))
    return items


def collect_log_config(data: dict[str, Any], game_dir: Path) -> DownloadItem | None:
    logging = data.get("logging")
    if not isinstance(logging, dict):
        return None
    client = logging.get("client")
    if not isinstance(client, dict):
        return None
    file_info = client.get("file")
    if not isinstance(file_info, dict):
        return None
    url = file_info.get("url")
    file_id = file_info.get("id")
    if not url or not file_id:
        return None
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(file_id))
    if not safe_id:
        return None
    return DownloadItem(
        url=str(url),
        dest=safe_join(game_dir / "assets" / "log_configs", safe_id),
        sha1=file_info.get("sha1"),
        size=_as_int(file_info.get("size")),
        label="日志配置",
        kind="log",
    )


def collect_asset_items(index_data: dict[str, Any], game_dir: Path) -> list[DownloadItem]:
    objects = index_data.get("objects")
    if not isinstance(objects, dict):
        return []
    assets_dir = game_dir / "assets" / "objects"
    items: list[DownloadItem] = []
    seen: set[str] = set()
    for _name, raw in objects.items():
        if not isinstance(raw, dict):
            continue
        digest = str(raw.get("hash") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{40}", digest):
            continue
        if digest in seen:
            continue
        seen.add(digest)
        dest = assets_dir / digest[:2] / digest
        items.append(
            DownloadItem(
                url=f"{ASSET_BASE}/{digest[:2]}/{digest}",
                dest=dest,
                sha1=digest,
                size=_as_int(raw.get("size")),
                label="游戏资源",
                kind="asset",
            )
        )
    return items


def collect_legacy_copies(
    index_data: dict[str, Any], game_dir: Path, index_id: str = ""
) -> list[tuple[Path, Path]]:
    """Return object -> resource mappings for virtual/legacy asset indexes."""
    virtual = bool(index_data.get("virtual"))
    map_to_resources = bool(index_data.get("map_to_resources"))
    if not virtual and not map_to_resources:
        return []
    objects = index_data.get("objects")
    if not isinstance(objects, dict):
        return []
    index_id = str(index_id or index_data.get("id") or "legacy")
    index_id = re.sub(r"[^A-Za-z0-9._-]+", "_", index_id) or "legacy"
    copies: list[tuple[Path, Path]] = []
    assets_objects = game_dir / "assets" / "objects"
    for name, raw in objects.items():
        if not isinstance(raw, dict):
            continue
        digest = str(raw.get("hash") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{40}", digest):
            continue
        source = assets_objects / digest[:2] / digest
        if map_to_resources:
            copies.append((source, safe_join(game_dir / "resources", str(name))))
        if virtual:
            copies.append(
                (source, safe_join(game_dir / "assets" / "virtual" / index_id, str(name)))
            )
    return copies


def safe_join(base: Path, relative: str) -> Path:
    relative = relative.replace("\\", "/")
    candidate = (base / relative).resolve(strict=False)
    base_resolved = base.resolve(strict=False)
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"不安全的文件路径：{relative[:120]}") from exc
    return candidate


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Progress and engine


@dataclass
class ProgressSnapshot:
    phase: str
    version: str
    version_index: int
    version_total: int
    completed_versions: int
    files_done: int
    files_total: int
    bytes_done: int
    bytes_total: int
    session_bytes: int
    speed: float
    overall: float
    message: str
    current_file: str = ""


@dataclass
class VersionFailure:
    version: str
    message: str


@dataclass
class EngineResult:
    requested: int
    installed: int
    skipped: int
    failed: int
    cancelled: bool
    failures: list[VersionFailure] = field(default_factory=list)
    session_bytes: int = 0
    elapsed: float = 0.0
    version_ids: list[str] = field(default_factory=list)
    version_elapsed: dict[str, float] = field(default_factory=dict)
    created_files: list[Path] = field(default_factory=list)


class SpeedMeter:
    def __init__(self) -> None:
        self._last_time = time.monotonic()
        self._last_bytes = 0
        self._speed = 0.0

    def update(self, total_bytes: int) -> float:
        now = time.monotonic()
        elapsed = now - self._last_time
        if elapsed >= 0.35:
            delta = max(0, total_bytes - self._last_bytes)
            instant = delta / elapsed
            self._speed = instant if self._speed <= 0 else self._speed * 0.7 + instant * 0.3
            self._last_time = now
            self._last_bytes = total_bytes
        return self._speed


class DownloadEngine:
    """Download a list of official versions into a launcher-compatible game root."""

    def __init__(
        self,
        game_dir: Path,
        mode: str,
        *,
        skip_existing: bool = True,
        workers: int = 32,
        cancel: threading.Event | None = None,
        on_progress: Callable[[ProgressSnapshot], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self.game_dir = game_dir
        self.mode = mode if mode in ("core", "full") else "core"
        self.skip_existing = skip_existing
        self.workers = max(1, min(32, int(workers)))
        self.cancel = cancel or threading.Event()
        self.on_progress = on_progress
        self.on_log = on_log or (lambda _message: None)
        self._lock = threading.Lock()
        self._session_bytes = 0
        self._current_bytes = 0
        self._current_total = 0
        self._files_done = 0
        self._files_total = 0
        self._current_file = ""
        self._phase = "prepare"
        self._message = ""
        self._current_version = ""
        self._version_index = 0
        self._version_total = 0
        self._completed_versions = 0
        self._speed = SpeedMeter()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._created_files: list[Path] = []
        self._created_lock = threading.Lock()

    # -- public ---------------------------------------------------------
    def run(self, entries: Iterable[VersionEntry]) -> EngineResult:
        entries = list(entries)
        started = time.monotonic()
        result = EngineResult(
            requested=len(entries), installed=0, skipped=0, failed=0, cancelled=False
        )
        result.version_ids = [entry.id for entry in entries]
        self._version_total = len(entries)
        errors: list[VersionFailure] = []

        for index, entry in enumerate(entries, start=1):
            if self.cancel.is_set():
                result.cancelled = True
                break
            version_started = time.monotonic()
            self._version_index = index
            self._current_version = entry.id
            self._current_bytes = 0
            self._current_total = 0
            self._files_done = 0
            self._files_total = 0
            self._current_file = ""
            self._phase = "prepare"
            self._message = f"准备 {entry.id}"
            self._emit()

            # Always create versions/<id> before any skip check so a fresh
            # download never silently reuses a half-prepared folder.
            if not self._ensure_version_dir(entry):
                result.version_elapsed[entry.id] = time.monotonic() - version_started
                message = f"无法创建版本目录 versions/{safe_version_id(entry.id)}"
                errors.append(VersionFailure(entry.id, message))
                result.failed += 1
                self._completed_versions += 1
                self._phase = "failed"
                self._message = f"{entry.id} 失败：{message}"
                self._emit(force=True)
                self.on_log(self._message)
                continue

            if self.skip_existing and self._already_complete(entry):
                self._completed_versions += 1
                result.skipped += 1
                self._phase = "done"
                self._message = f"{entry.id} 已安装，跳过"
                self._emit(force=True)
                self.on_log(self._message)
                continue

            try:
                self._install_version(entry)
            except DownloadCancelled:
                result.version_elapsed[entry.id] = time.monotonic() - version_started
                result.cancelled = True
                break
            except Exception as exc:
                result.version_elapsed[entry.id] = time.monotonic() - version_started
                message = str(exc) or exc.__class__.__name__
                errors.append(VersionFailure(entry.id, message))
                result.failed += 1
                self._completed_versions += 1
                self._phase = "failed"
                self._message = f"{entry.id} 失败：{message}"
                self._emit(force=True)
                self.on_log(self._message)
                continue

            result.version_elapsed[entry.id] = time.monotonic() - version_started
            self._completed_versions += 1
            result.installed += 1
            self._phase = "done"
            self._message = f"{entry.id} 下载完成"
            self._emit(force=True)
            self.on_log(self._message)

        self._stop_monitor()
        result.cancelled = result.cancelled or self.cancel.is_set()
        result.failures = errors
        result.session_bytes = self._session_bytes
        result.elapsed = time.monotonic() - started
        with self._created_lock:
            result.created_files = list(self._created_files)
        return result

    # -- progress -------------------------------------------------------
    def _emit(self, force: bool = False) -> None:
        if self.on_progress is None:
            return
        with self._lock:
            if self._current_total > 0:
                current_fraction = min(1.0, self._current_bytes / self._current_total)
            elif self._phase in ("prepare", "metadata", "scan"):
                current_fraction = 0.02
            elif self._phase in ("done", "failed"):
                current_fraction = 1.0
            else:
                current_fraction = 0.0
            if self._version_total:
                overall = (
                    self._completed_versions + current_fraction
                ) / self._version_total
            else:
                overall = 0.0
            snapshot = ProgressSnapshot(
                phase=self._phase,
                version=self._current_version,
                version_index=self._version_index,
                version_total=self._version_total,
                completed_versions=self._completed_versions,
                files_done=self._files_done,
                files_total=self._files_total,
                bytes_done=self._current_bytes,
                bytes_total=self._current_total,
                session_bytes=self._session_bytes,
                speed=self._speed.update(self._session_bytes),
                overall=max(0.0, min(1.0, overall)),
                message=self._message,
                current_file=self._current_file,
            )
        try:
            self.on_progress(snapshot)
        except Exception:
            pass

    def _set_phase(
        self, phase: str, message: str, *, current_file: str | None = None
    ) -> None:
        with self._lock:
            self._phase = phase
            self._message = message
            if current_file is not None:
                self._current_file = current_file
        self._emit(force=True)

    def _start_monitor(self) -> None:
        self._stop_monitor()
        self._monitor_stop = threading.Event()

        def monitor() -> None:
            while not self._monitor_stop.wait(0.15):
                self._emit()

        self._monitor_thread = threading.Thread(
            target=monitor, name="mcvd-progress", daemon=True
        )
        self._monitor_thread.start()

    def _stop_monitor(self) -> None:
        self._monitor_stop.set()
        thread = self._monitor_thread
        if thread and thread.is_alive():
            thread.join(timeout=0.6)
        self._monitor_thread = None

    # -- install --------------------------------------------------------
    def _install_version(self, entry: VersionEntry) -> None:
        if not self._ensure_version_dir(entry):
            raise DownloadError(f"无法创建版本目录 versions/{safe_version_id(entry.id)}")
        version_dir = self.game_dir / "versions" / safe_version_id(entry.id)
        version_dir.mkdir(parents=True, exist_ok=True)
        json_path = version_dir / f"{entry.id}.json"
        jar_path = version_dir / f"{entry.id}.jar"

        self._set_phase("metadata", f"读取 {entry.id} 的版本信息")
        self._ensure_version_json(entry, json_path)
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise DownloadError(f"版本 JSON 无法解析：{exc}") from exc

        client = (data.get("downloads") or {}).get("client")
        if not isinstance(client, dict) or not client.get("url"):
            raise DownloadError("该版本没有提供客户端 JAR")

        self._set_phase("scan", f"扫描 {entry.id} 的依赖文件")
        plan: list[DownloadItem] = []
        seen: set[str] = set()

        def add(item: DownloadItem | None) -> None:
            if item is None:
                return
            key = str(item.dest).casefold()
            if key in seen:
                return
            seen.add(key)
            plan.append(item)

        add(
            DownloadItem(
                url=str(client["url"]),
                dest=jar_path,
                sha1=client.get("sha1"),
                size=_as_int(client.get("size")),
                label="客户端主文件",
                kind="client",
            )
        )

        asset_index_data: dict[str, Any] | None = None
        asset_index_id = ""
        if self.mode == "full":
            for item in collect_library_items(data, self.game_dir):
                add(item)
            add(collect_log_config(data, self.game_dir))
            asset_index = data.get("assetIndex")
            if isinstance(asset_index, dict) and asset_index.get("url"):
                asset_index_id = str(asset_index.get("id") or data.get("assets") or "legacy")
                index_path = safe_join(
                    self.game_dir / "assets" / "indexes",
                    re.sub(r"[^A-Za-z0-9._-]+", "_", asset_index_id) + ".json",
                )
                index_item = DownloadItem(
                    url=str(asset_index["url"]),
                    dest=index_path,
                    sha1=asset_index.get("sha1"),
                    size=_as_int(asset_index.get("size")),
                    label="资源索引",
                    kind="asset_index",
                )
                self._ensure_asset_index(index_item)
                try:
                    asset_index_data = json.loads(index_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, UnicodeError) as exc:
                    raise DownloadError(f"资源索引无法解析：{exc}") from exc
                for item in collect_asset_items(asset_index_data, self.game_dir):
                    add(item)

        # Filter files that are already present before calculating totals.
        missing: list[DownloadItem] = []
        for item in plan:
            if self.skip_existing and net.file_is_usable(item.dest, item.size):
                continue
            missing.append(item)
        total_bytes = sum(item.size or 0 for item in missing)
        with self._lock:
            self._current_total = total_bytes
            self._files_total = len(missing)
            self._current_bytes = 0
            self._files_done = 0
        self._set_phase(
            "downloading",
            f"下载 {entry.id} · {len(missing)} 个文件",
            current_file=truncate_middle(missing[0].name, 90) if missing else "",
        )

        failures = self._download_all(missing)
        if self.cancel.is_set():
            raise DownloadCancelled()
        if failures:
            first = failures[0]
            raise DownloadError(
                f"{len(failures)} 个文件下载失败；{first}"
            )

        if self.mode == "full" and asset_index_data is not None:
            self._set_phase("finalizing", "整理旧版资源目录")
            self._materialize_legacy_assets(asset_index_data, asset_index_id)
            self._write_full_marker(entry, data, asset_index_id, len(plan))

        self._set_phase("done", f"{entry.id} 下载完成")

    def _ensure_version_json(self, entry: VersionEntry, path: Path) -> None:
        if path.is_file() and entry.sha1:
            try:
                if net.sha1_file(path, self.cancel).lower() == entry.sha1.lower():
                    return
            except OSError:
                pass
        item = DownloadItem(
            url=entry.url,
            dest=path,
            sha1=entry.sha1,
            size=None,
            label="版本信息",
            kind="json",
        )
        try:
            written = net.download_item(
                item,
                cancel=self.cancel,
                chunk_callback=self._chunk,
                attempts=3,
                force=True,
            )
            if written > 0:
                self._record_created(path)
        except DownloadCancelled:
            raise
        except DownloadError as exc:
            raise DownloadError(f"版本 JSON 下载失败：{exc}") from exc

    def _ensure_asset_index(self, item: DownloadItem) -> None:
        if item.dest.is_file() and item.sha1:
            try:
                if net.sha1_file(item.dest, self.cancel).lower() == item.sha1.lower():
                    return
            except OSError:
                pass
        self._set_phase("metadata", "下载资源索引", current_file="资源索引")
        try:
            written = net.download_item(
                item,
                cancel=self.cancel,
                chunk_callback=self._chunk,
                attempts=3,
                force=True,
            )
            if written > 0:
                self._record_created(item.dest)
        except DownloadCancelled:
            raise
        except DownloadError as exc:
            raise DownloadError(f"资源索引下载失败：{exc}") from exc

    def _download_all(self, items: list[DownloadItem]) -> list[str]:
        if not items:
            return []
        work: queue.Queue[DownloadItem] = queue.Queue()
        for item in items:
            work.put(item)
        failures: list[str] = []
        failures_lock = threading.Lock()
        self._start_monitor()

        def worker() -> None:
            while not self.cancel.is_set():
                try:
                    item = work.get_nowait()
                except queue.Empty:
                    return
                with self._lock:
                    self._current_file = truncate_middle(item.name, 100)
                try:
                    written = net.download_item(
                        item,
                        cancel=self.cancel,
                        chunk_callback=self._chunk,
                        attempts=3,
                    )
                except DownloadCancelled:
                    return
                except Exception as exc:
                    with failures_lock:
                        if len(failures) < 200:
                            failures.append(str(exc))
                else:
                    if written > 0:
                        self._record_created(item.dest)
                    with self._lock:
                        self._files_done += 1

        threads = [
            threading.Thread(target=worker, name=f"mcvd-dl-{i}", daemon=True)
            for i in range(min(self.workers, len(items)))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            while thread.is_alive():
                thread.join(timeout=0.25)
        self._stop_monitor()
        self._emit(force=True)
        return failures

    def _chunk(self, amount: int) -> None:
        with self._lock:
            self._current_bytes += amount
            self._session_bytes += amount

    def _record_created(self, path: Path) -> None:
        with self._created_lock:
            self._created_files.append(path)

    def _materialize_legacy_assets(
        self, index_data: dict[str, Any], asset_index_id: str
    ) -> None:
        copies = collect_legacy_copies(index_data, self.game_dir, asset_index_id)
        if not copies:
            return
        for index, (source, dest) in enumerate(copies, start=1):
            if self.cancel.is_set():
                raise DownloadCancelled()
            try:
                if not source.is_file():
                    raise FileNotFoundError(source.name)
                existed = dest.exists()
                net.copy_or_link(source, dest)
                if not existed:
                    self._record_created(dest)
            except OSError as exc:
                raise DownloadError(f"复制旧版资源失败：{dest.name}：{exc}") from exc
            if index % 200 == 0:
                self._set_phase(
                    "finalizing", f"整理旧版资源 · {index}/{len(copies)}"
                )

    def _write_full_marker(
        self,
        entry: VersionEntry,
        data: dict[str, Any],
        asset_index_id: str,
        planned_files: int,
    ) -> None:
        marker = {
            "format": 1,
            "app": APP_VERSION,
            "id": entry.id,
            "mode": "full",
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "asset_index": asset_index_id,
            "planned_files": planned_files,
            "client_sha1": ((data.get("downloads") or {}).get("client") or {}).get("sha1"),
        }
        path = self.game_dir / "versions" / entry.id / ".mcvdl_full.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, path)
        except OSError as exc:
            raise DownloadError(f"无法写入完成标记：{exc}") from exc

    # -- checks ---------------------------------------------------------
    def _ensure_version_dir(self, entry: VersionEntry) -> bool:
        """Create ``versions/<version id>`` before any download/skip check.

        Returns ``False`` when the folder could not be created so the caller
        can report the failure instead of quietly skipping the version.
        """
        version_dir = self.game_dir / "versions" / safe_version_id(entry.id)
        try:
            existed = version_dir.is_dir()
            version_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.on_log(f"{entry.id} 版本目录创建失败：{exc}")
            return False
        if not existed:
            self.on_log(f"已创建版本目录 versions/{version_dir.name}")
        return version_dir.is_dir()

    def _already_complete(self, entry: VersionEntry) -> bool:
        version_dir = self.game_dir / "versions" / safe_version_id(entry.id)
        json_path = version_dir / f"{entry.id}.json"
        jar_path = version_dir / f"{entry.id}.jar"
        if not version_dir.is_dir():
            return False
        if not json_path.is_file() or not jar_path.is_file():
            return False
        if jar_path.stat().st_size <= 0:
            return False
        if self.mode == "core":
            return True
        marker = version_dir / ".mcvdl_full.json"
        if not marker.is_file():
            return False
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return data.get("mode") == "full" and data.get("format") == 1


def delete_created_files(
    game_dir: Path, files: Iterable[Path]
) -> tuple[int, int]:
    """Delete only files created by the current run, then prune empty dirs."""
    root = game_dir.resolve(strict=False)
    removed = 0
    failed = 0
    for raw in files:
        try:
            candidate = Path(raw).resolve(strict=False)
            candidate.relative_to(root)
        except (OSError, ValueError):
            failed += 1
            continue
        try:
            if candidate.is_file():
                candidate.unlink()
                removed += 1
        except OSError:
            failed += 1
            continue
        parent = candidate.parent
        while parent != root:
            try:
                parent.relative_to(root)
            except ValueError:
                break
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    return removed, failed


def scan_local_versions(game_dir: Path) -> dict[str, str]:
    """Return ``version id -> state`` for a game root."""
    result: dict[str, str] = {}
    versions_dir = game_dir / "versions"
    try:
        children = list(versions_dir.iterdir())
    except OSError:
        return result
    for child in children:
        try:
            if not child.is_dir():
                continue
            version_id = child.name
            json_path = child / f"{version_id}.json"
            jar_path = child / f"{version_id}.jar"
            has_json = json_path.is_file() and json_path.stat().st_size > 0
            has_jar = jar_path.is_file() and jar_path.stat().st_size > 0
            if has_json and has_jar:
                result[version_id] = "installed"
            elif has_json or has_jar:
                result[version_id] = "partial"
        except OSError:
            continue
    return result


def display_version_type(entry: VersionEntry, latest_release: str | None) -> str:
    label = entry.type_label
    if latest_release and entry.id == latest_release:
        label += " · 最新"
    return label


def format_progress_detail(snapshot: ProgressSnapshot) -> str:
    pieces: list[str] = []
    if snapshot.files_total:
        pieces.append(f"{snapshot.files_done}/{snapshot.files_total} 个文件")
    if snapshot.bytes_total:
        pieces.append(
            f"{format_bytes(snapshot.bytes_done)} / {format_bytes(snapshot.bytes_total)}"
        )
    if snapshot.speed:
        pieces.append(format_bytes(snapshot.speed) + "/s")
    return " · ".join(pieces)
