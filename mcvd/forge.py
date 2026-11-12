"""Forge and NeoForge installer support.

Both projects ship an official installer jar.  The installer knows how to
download the loader libraries and run its patch processors, but on some
networks (notably mainland China) the NeoForge maven host is unreachable.
We therefore preload the install-profile libraries from BMCLAPI/Maven Central
first, then let the official installer run offline for those files.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from . import net
from .config import APP_VERSION, cache_dir
from .core import safe_join
from .java import ensure_java
from .net import DownloadCancelled, DownloadError, DownloadItem

FORGE_METADATA_URLS = (
    "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml",
    "https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge/maven-metadata.xml",
    "https://mirrors.qlu.edu.cn/bmclapi/net/minecraftforge/forge/maven-metadata.xml",
    "https://mirror4.nyist.edu.cn/bmclapi/net/minecraftforge/forge/maven-metadata.xml",
)
FORGE_PROMOTIONS_URLS = (
    "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json",
)
NEOFORGE_METADATA_URLS = (
    "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml",
    "https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge/maven-metadata.xml",
    "https://mirrors.qlu.edu.cn/bmclapi/net/neoforged/neoforge/maven-metadata.xml",
    "https://mirror4.nyist.edu.cn/bmclapi/net/neoforged/neoforge/maven-metadata.xml",
)
BMCLAPI_MAVEN = "https://bmclapi2.bangbang93.com/maven/"
# The main BMCLAPI endpoint sometimes rate-limits or returns 403.  These are
# official BMCLAPI mirrors of the same maven tree; the path prefix differs.
MAVEN_MIRRORS = (
    "https://bmclapi2.bangbang93.com/maven/",
    "https://mirrors.qlu.edu.cn/bmclapi/",
    "https://mirror4.nyist.edu.cn/bmclapi/",
    "https://mirrors4.qlu.edu.cn/bmclapi/",
)
MAVEN_CENTRAL = "https://repo1.maven.org/maven2/"
CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class ForgeRelease:
    kind: str
    mc_version: str
    version: str
    stable: bool
    installer_urls: tuple[str, ...]
    java_major: int


def java_major_for_mc(mc_version: str) -> int:
    """Java major used by the Minecraft version (Forge/NeoForge follow it)."""
    parts = _version_tuple(mc_version)
    if not parts:
        return 21
    if parts[0] >= 26:
        return 25
    if parts >= (1, 20, 5):
        return 21
    if parts >= (1, 18):
        return 17
    return 8


def _version_tuple(value: str) -> tuple[int, ...]:
    result: list[int] = []
    for piece in re.split(r"[.\-+]", value):
        if piece.isdigit():
            result.append(int(piece))
        elif result:
            break
    return tuple(result)


def _stable_forge(version: str) -> bool:
    return "beta" not in version.lower() and "alpha" not in version.lower()


def _fetch_first(urls: Iterable[str], cancel: threading.Event | None) -> bytes:
    last: Exception | None = None
    for url in urls:
        try:
            return net.fetch_bytes(url, timeout=20, cancel=cancel, attempts=1)
        except DownloadCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise DownloadError(f"加载器元数据获取失败：{last}")


def _metadata_text(urls: Iterable[str], cancel: threading.Event | None) -> str:
    return _fetch_first(urls, cancel).decode("utf-8", "replace")


def _xml_versions(text: str) -> list[str]:
    return re.findall(r"<version>([^<]+)</version>", text)


def _promotions(cancel: threading.Event | None) -> dict[str, str]:
    try:
        payload = _fetch_first(FORGE_PROMOTIONS_URLS, cancel)
        data = json.loads(payload.decode("utf-8"))
    except DownloadCancelled:
        raise
    except Exception:
        return {}
    promos = data.get("promos") if isinstance(data, dict) else None
    if not isinstance(promos, dict):
        return {}
    return {str(key): str(value) for key, value in promos.items()}


def list_forge_versions(
    mc_version: str, cancel: threading.Event | None = None
) -> list[ForgeRelease]:
    promos = _promotions(cancel)
    recommended = promos.get(f"{mc_version}-recommended", "")
    latest = promos.get(f"{mc_version}-latest", "")
    releases: list[ForgeRelease] = []
    try:
        text = _metadata_text(FORGE_METADATA_URLS, cancel)
        raw_versions = _xml_versions(text)
    except DownloadError:
        raw_versions = []
    prefix = mc_version + "-"
    seen: set[str] = set()
    for raw in raw_versions:
        if not raw.startswith(prefix):
            continue
        version = raw[len(prefix) :]
        if not version or version in seen:
            continue
        if not re.fullmatch(r"[0-9][0-9A-Za-z.\-+]*", version):
            continue
        seen.add(version)
        relative = (
            f"net/minecraftforge/forge/{mc_version}-{version}/"
            f"forge-{mc_version}-{version}-installer.jar"
        )
        urls = (
            f"https://maven.minecraftforge.net/{relative}",
            *_mirror_urls(relative),
        )
        releases.append(
            ForgeRelease(
                kind="forge",
                mc_version=mc_version,
                version=version,
                stable=_stable_forge(version),
                installer_urls=urls,
                java_major=java_major_for_mc(mc_version),
            )
        )
    if not releases:
        for version in (recommended, latest):
            if not version or version in seen:
                continue
            seen.add(version)
            relative = (
                f"net/minecraftforge/forge/{mc_version}-{version}/"
                f"forge-{mc_version}-{version}-installer.jar"
            )
            urls = (
                f"https://maven.minecraftforge.net/{relative}",
                *_mirror_urls(relative),
            )
            releases.append(
                ForgeRelease(
                    kind="forge",
                    mc_version=mc_version,
                    version=version,
                    stable=True,
                    installer_urls=urls,
                    java_major=java_major_for_mc(mc_version),
                )
            )
    releases.sort(
        key=lambda item: (
            item.stable,
            _version_tuple(item.version),
            item.version,
        ),
        reverse=True,
    )
    return releases


def _neoforge_mc(version: str) -> str | None:
    parts = _version_tuple(version)
    if len(parts) < 2:
        return None
    if parts[0] >= 26:
        if len(parts) >= 3:
            return f"26.{parts[1]}.{parts[2]}"
        return f"26.{parts[1]}"
    if parts[0] == 21:
        return f"1.21.{parts[1]}"
    if parts[0] == 20:
        return f"1.20.{parts[1]}"
    return None


def list_neoforge_versions(
    mc_version: str, cancel: threading.Event | None = None
) -> list[ForgeRelease]:
    text = _metadata_text(NEOFORGE_METADATA_URLS, cancel)
    releases: list[ForgeRelease] = []
    for version in _xml_versions(text):
        if not version or "alpha" in version.lower() or "beta" in version.lower():
            continue
        if _neoforge_mc(version) != mc_version:
            continue
        relative = (
            f"net/neoforged/neoforge/{version}/"
            f"neoforge-{version}-installer.jar"
        )
        urls = (
            f"https://maven.neoforged.net/releases/{relative}",
            *_mirror_urls(relative),
        )
        releases.append(
            ForgeRelease(
                kind="neoforge",
                mc_version=mc_version,
                version=version,
                stable=True,
                installer_urls=urls,
                java_major=java_major_for_mc(mc_version),
            )
        )
    releases.sort(key=lambda item: (_version_tuple(item.version), item.version), reverse=True)
    return releases


def _mirror_urls(relative_path: str) -> list[str]:
    relative = relative_path.replace("\\", "/")
    return [base + relative for base in MAVEN_MIRRORS]


def _artifact_urls(url: str, relative_path: str) -> list[str]:
    urls: list[str] = []
    if "maven.neoforged.net/releases/" in url:
        urls.extend(
            _mirror_urls(url.split("maven.neoforged.net/releases/", 1)[1])
        )
        urls.append(url)
    elif "maven.minecraftforge.net/" in url:
        urls.append(url)
        urls.extend(
            _mirror_urls(url.split("maven.minecraftforge.net/", 1)[1])
        )
    else:
        urls.append(url)
    if relative_path:
        urls.append(MAVEN_CENTRAL + relative_path.replace("\\", "/"))
    # Keep order, remove duplicates.
    return list(dict.fromkeys(urls))


def _download_to_cache(
    urls: Iterable[str], destination: Path, cancel: threading.Event
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 1024:
        return destination
    last: Exception | None = None
    for url in urls:
        try:
            net.download_item(
                DownloadItem(
                    url=url,
                    dest=destination,
                    label=destination.name,
                    kind="loader",
                ),
                cancel=cancel,
                attempts=2,
            )
            if destination.is_file() and destination.stat().st_size > 1024:
                return destination
        except DownloadCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
    raise DownloadError(f"安装器下载失败：{last}")


def _read_profile(installer: Path) -> dict:
    try:
        with zipfile.ZipFile(installer) as archive:
            payload = archive.read("install_profile.json")
        profile = json.loads(payload.decode("utf-8"))
    except (OSError, KeyError, UnicodeError, ValueError) as exc:
        raise DownloadError(f"安装器 install_profile.json 无法读取：{exc}") from exc
    if not isinstance(profile, dict):
        raise DownloadError("安装器 install_profile.json 格式不正确")
    return profile


def _snapshot(root: Path) -> set[str]:
    found: set[str] = set()
    try:
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    found.add(str(path.resolve(strict=False)))
            except OSError:
                continue
    except OSError:
        pass
    return found


def _download_artifacts(
    jobs: list[tuple[DownloadItem, list[str]]],
    *,
    cancel: threading.Event,
    workers: int,
    progress: Callable[[float, str], None],
) -> list[str]:
    if not jobs:
        progress(1.0, "安装器库文件已就绪")
        return []
    total = sum(item.size or 0 for item, _urls in jobs)
    done_bytes = 0
    done_files = 0
    failures: list[str] = []
    current = [""]
    lock = threading.Lock()
    last_emit = [0.0]

    def emit(force: bool = False) -> None:
        now = time.monotonic()
        with lock:
            if not force and now - last_emit[0] < 0.12:
                return
            last_emit[0] = now
            fraction = (
                min(1.0, done_bytes / total)
                if total
                else min(1.0, done_files / max(1, len(jobs)))
            )
            message = current[0] or f"安装器库文件 {done_files}/{len(jobs)}"
        try:
            progress(fraction, message)
        except Exception:
            pass

    work: queue.Queue[tuple[DownloadItem, list[str]]] = queue.Queue()
    for job in jobs:
        work.put(job)

    def worker() -> None:
        nonlocal done_bytes, done_files
        while not cancel.is_set():
            try:
                item, urls = work.get_nowait()
            except queue.Empty:
                return
            with lock:
                current[0] = f"安装器库 · {item.name}"
            emit(force=True)
            last_error: Exception | None = None
            for url in urls:
                if cancel.is_set():
                    return
                trial = DownloadItem(
                    url=url,
                    dest=item.dest,
                    sha1=item.sha1,
                    size=item.size,
                    label=item.label,
                    kind=item.kind,
                )
                try:
                    net.download_item(
                        trial,
                        cancel=cancel,
                        chunk_callback=lambda amount: _add(amount),
                        attempts=2,
                    )
                except DownloadCancelled:
                    return
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    continue
                with lock:
                    done_files += 1
                emit()
                break
            else:
                with lock:
                    if len(failures) < 30:
                        failures.append(f"{item.name}: {last_error}")
            emit()

    def _add(amount: int) -> None:
        nonlocal done_bytes
        with lock:
            done_bytes += amount
        emit()

    threads = [
        threading.Thread(target=worker, name=f"mcvd-installer-{i}", daemon=True)
        for i in range(min(max(1, workers), len(jobs)))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    emit(force=True)
    if cancel.is_set():
        raise DownloadCancelled()
    return failures


def _prepare_launcher_profile(game_dir: Path) -> bytes | None:
    path = game_dir / "launcher_profiles.json"
    backup: bytes | None = None
    if path.is_file():
        try:
            backup = path.read_bytes()
        except OSError:
            backup = None
    if backup is None:
        path.write_text(
            json.dumps({"profiles": {}, "settings": {}, "version": 3}),
            encoding="utf-8",
        )
    return backup


def _restore_launcher_profile(game_dir: Path, backup: bytes | None) -> None:
    path = game_dir / "launcher_profiles.json"
    try:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(backup)
    except OSError:
        pass


def _run_installer(
    java: Path,
    installer: Path,
    game_dir: Path,
    *,
    cancel: threading.Event,
    progress: Callable[[float, str], None],
    status: Callable[[str], None],
) -> None:
    backup = _prepare_launcher_profile(game_dir)
    command = [
        str(java),
        "-jar",
        str(installer),
        "--installClient",
        str(game_dir),
    ]
    creationflags = CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(game_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except OSError:
        _restore_launcher_profile(game_dir, backup)
        raise
    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    lines.put(line.rstrip())
        finally:
            lines.put(None)

    thread = threading.Thread(target=reader, name="mcvd-forge-output", daemon=True)
    thread.start()
    recent: list[str] = []
    started = time.monotonic()
    last_emit = 0.0
    try:
        while True:
            if cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise DownloadCancelled()
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line:
                recent.append(line)
                del recent[:-25]
                if line.startswith(("Processor:", "Injecting", "Successfully")):
                    clean = line.strip()
                    status(f"安装器：{clean[:100]}")
                now = time.monotonic()
                if now - last_emit >= 0.4:
                    last_emit = now
                    progress(
                        min(0.95, 0.62 + (now - started) / 180.0 * 0.30),
                        f"运行安装器 · {recent[-1][:90]}" if recent else "运行安装器",
                    )
            if proc.poll() is not None:
                # Drain anything already buffered, then stop.
                while True:
                    try:
                        extra = lines.get_nowait()
                    except queue.Empty:
                        break
                    if extra:
                        recent.append(extra)
                        del recent[:-25]
                break
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = -1
    finally:
        _restore_launcher_profile(game_dir, backup)
    if code != 0:
        detail = " | ".join(recent[-4:])
        raise DownloadError(f"安装器返回错误码 {code}：{detail}")


def _find_installed_version(
    game_dir: Path, mc_version: str, kind: str, version: str
) -> tuple[str, Path] | None:
    versions_dir = game_dir / "versions"
    preferred = (
        f"{mc_version}-forge-{version}"
        if kind == "forge"
        else f"neoforge-{version}"
    )
    candidates = [preferred]
    try:
        candidates.extend(
            sorted(
                (path.name for path in versions_dir.iterdir() if path.is_dir()),
                key=lambda name: (versions_dir / name).stat().st_mtime,
                reverse=True,
            )
        )
    except OSError:
        pass
    for name in candidates:
        directory = versions_dir / name
        json_path = directory / f"{name}.json"
        if not json_path.is_file():
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("inheritsFrom") != mc_version:
            continue
        loader_id = str(data.get("id") or name)
        if kind == "forge" and "forge" not in loader_id.lower():
            continue
        if kind == "neoforge" and "neoforge" not in loader_id.lower():
            continue
        if kind == "neoforge" and version not in loader_id:
            continue
        return name, json_path
    return None


def install_forge_like(
    game_dir: Path,
    mc_version: str,
    kind: str,
    version: str,
    installer_urls: Iterable[str],
    java_major: int,
    *,
    cancel: threading.Event,
    workers: int = 12,
    on_progress: Callable[[float, str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    created_files: list[Path] | None = None,
) -> str:
    """Install Forge or NeoForge through its official headless installer."""
    progress = on_progress or (lambda _fraction, _message: None)
    status = on_status or (lambda _message: None)
    created = created_files if created_files is not None else []
    before = _snapshot(game_dir)
    try:
        status(f"准备 {kind} {version} 安装器")
        progress(0.02, f"下载 {kind} 安装器")
        cache = cache_dir() / "installers"
        filename = (
            f"forge-{mc_version}-{version}-installer.jar"
            if kind == "forge"
            else f"neoforge-{version}-installer.jar"
        )
        installer = _download_to_cache(
            installer_urls, cache / filename, cancel
        )
        status("检查 Java 运行环境")
        progress(0.08, f"检查 Java {java_major}")
        java = ensure_java(
            java_major,
            cancel=cancel,
            on_progress=lambda fraction, message: progress(
                0.08 + fraction * 0.08, message
            ),
            on_status=status,
        )

        status("读取安装器依赖")
        progress(0.17, "读取安装器依赖")
        profile = _read_profile(installer)
        libraries = profile.get("libraries")
        if not isinstance(libraries, list):
            raise DownloadError("安装器缺少 libraries 信息")
        jobs: list[tuple[DownloadItem, list[str]]] = []
        for library in libraries:
            if not isinstance(library, dict):
                continue
            downloads = library.get("downloads") or {}
            artifact = downloads.get("artifact") if isinstance(downloads, dict) else None
            if not isinstance(artifact, dict):
                continue
            url = str(artifact.get("url") or "")
            relative = str(artifact.get("path") or "")
            if not url or not relative:
                continue
            destination = safe_join(game_dir / "libraries", relative)
            if net.file_is_usable(destination, _as_int(artifact.get("size"))):
                continue
            item = DownloadItem(
                url=url,
                dest=destination,
                sha1=str(artifact.get("sha1") or "") or None,
                size=_as_int(artifact.get("size")),
                label=f"{kind} 库 · {destination.name}",
                kind="loader",
            )
            jobs.append((item, _artifact_urls(url, relative)))
        status(f"下载 {kind} 安装依赖（{len(jobs)} 个文件）")
        failures = _download_artifacts(
            jobs,
            cancel=cancel,
            workers=workers,
            progress=lambda fraction, message: progress(
                0.18 + fraction * 0.40, message
            ),
        )
        if failures:
            raise DownloadError(f"{len(failures)} 个安装器依赖失败：{failures[0]}")

        status(f"运行 {kind} 安装器（需要几分钟）")
        progress(0.60, "运行安装器，请稍候")
        _run_installer(
            java,
            installer,
            game_dir,
            cancel=cancel,
            progress=progress,
            status=status,
        )
        found = _find_installed_version(game_dir, mc_version, kind, version)
        if found is None:
            raise DownloadError("安装器已完成，但没有找到生成的加载器版本")
        version_id, json_path = found
        marker = {
            "format": 1,
            "app": APP_VERSION,
            "type": "loader",
            "loader": kind,
            "loader_version": version,
            "base_version": mc_version,
            "installer": str(installer),
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        marker_path = json_path.parent / ".mcvdl_loader.json"
        marker_path.write_text(
            json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        progress(1.0, f"已安装 {version_id}")
        status(f"已创建版本 {version_id}")
        return version_id
    finally:
        # Snapshot difference also covers partial files when the user cancels.
        after = _snapshot(game_dir)
        for path in sorted(after - before):
            try:
                created.append(Path(path))
            except OSError:
                continue


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
