"""Fabric / Quilt loader metadata and loader installation."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import net
from .config import APP_VERSION, safe_version_id, truncate_middle
from .core import (
    DownloadEngine,
    VersionEntry,
    collect_library_items,
    safe_join,
)
from .forge import (
    ForgeRelease,
    install_forge_like,
    list_forge_versions,
    list_neoforge_versions,
)
from .net import DownloadCancelled, DownloadError, DownloadItem

LOADER_LABELS = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
}
LOADER_ORDER = ("fabric", "quilt", "forge", "neoforge")

FABRIC_API = "https://meta.fabricmc.net/v2/versions/loader/{mc}"
FABRIC_PROFILE = "https://meta.fabricmc.net/v2/versions/loader/{mc}/{loader}/profile/json"
QUILT_API = "https://meta.quiltmc.org/v3/versions/loader/{mc}"
QUILT_PROFILE = "https://meta.quiltmc.org/v3/versions/loader/{mc}/{loader}/profile/json"


@dataclass(frozen=True)
class LoaderChoice:
    kind: str
    mc_version: str
    version: str
    stable: bool
    profile_url: str = ""
    installer_urls: tuple[str, ...] = ()
    java_major: int = 0

    @property
    def label(self) -> str:
        suffix = "" if self.stable else " 测试"
        return f"{self.version}{suffix}"


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _choice_from_release(release: ForgeRelease) -> LoaderChoice:
    return LoaderChoice(
        kind=release.kind,
        mc_version=release.mc_version,
        version=release.version,
        stable=release.stable,
        installer_urls=release.installer_urls,
        java_major=release.java_major,
    )


def list_loader_versions(
    kind: str, mc_version: str, cancel: threading.Event | None = None
) -> list[LoaderChoice]:
    if kind not in LOADER_LABELS:
        raise DownloadError("暂不支持该模组加载器")
    if kind == "forge":
        return [
            _choice_from_release(release)
            for release in list_forge_versions(mc_version, cancel)
        ]
    if kind == "neoforge":
        return [
            _choice_from_release(release)
            for release in list_neoforge_versions(mc_version, cancel)
        ]
    mc = _quote(mc_version)
    if kind == "fabric":
        payload = net.fetch_bytes(
            FABRIC_API.format(mc=mc), cancel=cancel, attempts=3
        )
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, list):
            raise DownloadError("Fabric 加载器列表格式不正确")
        choices: list[LoaderChoice] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            loader = item.get("loader") if isinstance(item.get("loader"), dict) else item
            version = loader.get("version")
            if not version:
                continue
            version = str(version)
            choices.append(
                LoaderChoice(
                    kind=kind,
                    mc_version=mc_version,
                    version=version,
                    stable=bool(loader.get("stable", False)),
                    profile_url=FABRIC_PROFILE.format(
                        mc=mc, loader=_quote(version)
                    ),
                )
            )
        return choices
    payload = net.fetch_bytes(QUILT_API.format(mc=mc), cancel=cancel, attempts=3)
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, list):
        raise DownloadError("Quilt 加载器列表格式不正确")
    choices = []
    for item in data:
        if not isinstance(item, dict):
            continue
        loader = item.get("loader")
        if not isinstance(loader, dict) or not loader.get("version"):
            continue
        version = str(loader["version"])
        choices.append(
            LoaderChoice(
                kind=kind,
                mc_version=mc_version,
                version=version,
                stable="beta" not in version.lower(),
                profile_url=QUILT_PROFILE.format(mc=mc, loader=_quote(version)),
            )
        )
    return choices


def fetch_loader_profile(
    choice: LoaderChoice, cancel: threading.Event | None = None
) -> dict:
    payload = net.fetch_bytes(choice.profile_url, cancel=cancel, attempts=3)
    try:
        profile = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise DownloadError(f"加载器配置无法解析：{exc}") from exc
    if not isinstance(profile, dict):
        raise DownloadError("加载器配置格式不正确")
    if not profile.get("id") or not isinstance(profile.get("libraries"), list):
        raise DownloadError("加载器配置缺少版本 id 或库信息")
    return profile


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _download_loader_libraries(
    items: list[DownloadItem],
    *,
    cancel: threading.Event,
    workers: int,
    progress: Callable[[float, str], None],
    created_files: list[Path] | None = None,
) -> list[str]:
    total_bytes = sum(item.size or 0 for item in items)
    total_files = len(items)
    done_bytes = 0
    done_files = 0
    current = [""]
    lock = threading.Lock()
    last_emit = [0.0]
    failures: list[str] = []

    def emit(force: bool = False) -> None:
        now = time.monotonic()
        with lock:
            if not force and now - last_emit[0] < 0.12:
                return
            last_emit[0] = now
            if total_bytes:
                fraction = min(1.0, done_bytes / total_bytes)
            elif total_files:
                fraction = min(1.0, done_files / total_files)
            else:
                fraction = 1.0
            message = current[0] or f"模组库 {done_files}/{total_files}"
        try:
            progress(fraction, message)
        except Exception:
            pass

    if not items:
        progress(1.0, "模组库已就绪")
        return []

    work: queue.Queue[DownloadItem] = queue.Queue()
    for item in items:
        work.put(item)

    def chunk(amount: int) -> None:
        nonlocal done_bytes
        with lock:
            done_bytes += amount
        emit()

    def worker() -> None:
        nonlocal done_files
        while not cancel.is_set():
            try:
                item = work.get_nowait()
            except queue.Empty:
                return
            with lock:
                current[0] = f"模组库 · {truncate_middle(item.name, 80)}"
            emit(force=True)
            try:
                net.download_item(
                    item,
                    cancel=cancel,
                    chunk_callback=chunk,
                    attempts=3,
                )
            except DownloadCancelled:
                return
            except Exception as exc:
                with lock:
                    if len(failures) < 100:
                        failures.append(str(exc))
            else:
                with lock:
                    done_files += 1
                    if created_files is not None and item.dest not in created_files:
                        created_files.append(item.dest)
                emit()

    threads = [
        threading.Thread(target=worker, name=f"mcvd-loader-{i}", daemon=True)
        for i in range(min(workers, len(items)))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    emit(force=True)
    if cancel.is_set():
        raise DownloadCancelled()
    return failures


def install_loader_files(
    game_dir: Path,
    base_entry: VersionEntry,
    choice: LoaderChoice,
    *,
    workers: int = 32,
    cancel: threading.Event | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    created_files: list[Path] | None = None,
) -> tuple[str, float]:
    """Add the selected loader to an already downloaded vanilla version.

    Returns ``(version_id, elapsed_seconds)``.  Newly written game files are
    appended to ``created_files`` so the caller can offer cancellation cleanup.
    """
    cancel = cancel or threading.Event()
    progress = on_progress or (lambda _fraction, _message: None)
    status = on_status or (lambda _message: None)
    created = created_files if created_files is not None else []
    started = time.monotonic()

    if choice.kind in ("forge", "neoforge"):
        status(f"准备 {LOADER_LABELS[choice.kind]} {choice.version}")
        progress(0.01, f"准备 {LOADER_LABELS[choice.kind]} 安装")
        version_id = install_forge_like(
            game_dir,
            choice.mc_version,
            choice.kind,
            choice.version,
            choice.installer_urls,
            choice.java_major,
            cancel=cancel,
            workers=min(24, workers),
            on_progress=progress,
            on_status=status,
            created_files=created,
        )
        return version_id, time.monotonic() - started

    status("下载加载器配置")
    progress(0.01, f"正在获取 {LOADER_LABELS.get(choice.kind, choice.kind)} 配置")
    profile = fetch_loader_profile(choice, cancel)
    version_id = safe_version_id(str(profile.get("id", "")))
    if not version_id:
        raise DownloadError("加载器配置缺少有效的版本 id")
    profile["inheritsFrom"] = base_entry.id
    profile["id"] = version_id
    profile["type"] = profile.get("type") or "release"

    status("下载加载器库文件")
    progress(0.05, "扫描加载器库文件")
    try:
        library_items = collect_library_items(profile, game_dir)
    except ValueError as exc:
        raise DownloadError(f"加载器库路径无效：{exc}") from exc
    missing = [
        item
        for item in library_items
        if not net.file_is_usable(item.dest, item.size)
    ]
    failures = _download_loader_libraries(
        missing,
        cancel=cancel,
        workers=workers,
        progress=lambda fraction, message: progress(
            0.05 + fraction * 0.88, message
        ),
        created_files=created,
    )
    if failures:
        raise DownloadError(f"{len(failures)} 个加载器库文件下载失败；{failures[0]}")

    version_dir = game_dir / "versions" / version_id
    json_path = version_dir / f"{version_id}.json"
    if not json_path.exists():
        created.append(json_path)
    _write_json(json_path, profile)
    marker_path = version_dir / ".mcvdl_loader.json"
    marker = {
        "format": 1,
        "app": APP_VERSION,
        "type": "loader",
        "loader": choice.kind,
        "loader_version": choice.version,
        "base_version": base_entry.id,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    if not marker_path.exists():
        created.append(marker_path)
    _write_json(marker_path, marker)
    progress(1.0, f"已合并 {version_id}")
    status(f"已创建版本 {version_id}")
    return version_id, time.monotonic() - started


def install_loader(
    game_dir: Path,
    base_entry: VersionEntry,
    choice: LoaderChoice,
    *,
    mode: str = "full",
    workers: int = 32,
    cancel: threading.Event | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> str:
    """Backward-compatible full install (base + loader). Used by older code paths."""
    cancel = cancel or threading.Event()
    progress = on_progress or (lambda _fraction, _message: None)
    status = on_status or (lambda _message: None)
    status(f"准备基础版本 {base_entry.id}")
    progress(0.01, f"准备基础版本 {base_entry.id}")

    def base_progress(snapshot) -> None:
        progress(
            0.02 + max(0.0, min(1.0, snapshot.overall)) * 0.68,
            f"基础版本 · {snapshot.message}",
        )

    engine = DownloadEngine(
        game_dir,
        mode,
        skip_existing=True,
        workers=workers,
        cancel=cancel,
        on_progress=base_progress,
    )
    result = engine.run([base_entry])
    if result.cancelled or cancel.is_set():
        raise DownloadCancelled()
    if result.failed:
        raise DownloadError(
            f"基础版本 {base_entry.id} 下载失败："
            + (result.failures[0].message if result.failures else "未知错误")
        )
    version_id, _elapsed = install_loader_files(
        game_dir,
        base_entry,
        choice,
        workers=workers,
        cancel=cancel,
        on_progress=lambda fraction, message: progress(
            0.69 + fraction * 0.31, message
        ),
        on_status=status,
    )
    return version_id
