"""Download complete Mojang Java runtimes (the same runtimes used by the launcher)."""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import net
from .config import APP_VERSION, app_data_root
from .net import DownloadCancelled, DownloadError, DownloadItem

JAVA_RUNTIME_INDEX = (
    "https://launchermeta.mojang.com/v1/products/java-runtime/"
    "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json"
)
JAVA_COMPONENTS = {
    8: "jre-legacy",
    17: "java-runtime-gamma",
    21: "java-runtime-delta",
    25: "java-runtime-epsilon",
}
JAVA_MAJORS = tuple(JAVA_COMPONENTS)
JAVA_IMAGE_LABELS = {"jre": "JRE（完整运行时）"}


@dataclass(frozen=True)
class JavaPackage:
    major: int
    component: str
    version: str
    manifest_url: str
    manifest_sha1: str
    total_size: int
    file_count: int
    image_type: str = "jre"

    @property
    def display_size(self) -> str:
        size = float(self.total_size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{size:.1f} GB"


@dataclass(frozen=True)
class JavaInstall:
    major: int
    image_type: str
    version: str
    directory: Path
    java_exe: Path
    archived_size: int


def default_java_root() -> Path:
    root = app_data_root() / "java"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def fetch_java_package(
    major: int,
    image_type: str = "jre",
    cancel: threading.Event | None = None,
) -> JavaPackage:
    if major not in JAVA_COMPONENTS:
        raise DownloadError("暂不支持该 Java 版本")
    component = JAVA_COMPONENTS[major]
    payload = net.fetch_bytes(JAVA_RUNTIME_INDEX, cancel=cancel, attempts=3)
    try:
        index = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise DownloadError(f"Java 运行时索引无法解析：{exc}") from exc
    entries = (index.get("windows-x64") or {}).get(component) if isinstance(index, dict) else None
    if not isinstance(entries, list) or not entries:
        raise DownloadError(f"没有找到适用于 Windows x64 的 Java {major} 运行时")
    entry = entries[0]
    manifest = entry.get("manifest") if isinstance(entry, dict) else None
    if not isinstance(manifest, dict) or not manifest.get("url"):
        raise DownloadError("Java 运行时缺少 manifest 地址")
    manifest_url = str(manifest["url"])
    data = net.fetch_bytes(manifest_url, cancel=cancel, attempts=3)
    try:
        manifest_data = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise DownloadError(f"Java manifest 无法解析：{exc}") from exc
    files = manifest_data.get("files") if isinstance(manifest_data, dict) else None
    if not isinstance(files, dict):
        raise DownloadError("Java manifest 格式不正确")
    total_size = 0
    file_count = 0
    for meta in files.values():
        if not isinstance(meta, dict) or meta.get("type") != "file":
            continue
        downloads = meta.get("downloads") or {}
        compressed = downloads.get("lzma") or downloads.get("raw")
        if isinstance(compressed, dict):
            try:
                total_size += int(compressed.get("size") or 0)
            except (TypeError, ValueError):
                pass
            file_count += 1
    version = ""
    if isinstance(entry.get("version"), dict):
        version = str(entry["version"].get("name") or "")
    version = version or f"Java {major}"
    return JavaPackage(
        major=major,
        component=component,
        version=version,
        manifest_url=manifest_url,
        manifest_sha1=str(manifest.get("sha1") or ""),
        total_size=total_size,
        file_count=file_count,
        image_type="jre",
    )


def download_java(
    package: JavaPackage,
    target_root: Path,
    *,
    cancel: threading.Event | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    workers: int = 24,
) -> JavaInstall:
    """Download every file of a Mojang Java runtime and decompress it."""
    cancel = cancel or threading.Event()
    progress = on_progress or (lambda _fraction, _message: None)
    target_root = Path(target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    final_dir = target_root / f"java-{package.major}"

    progress(0.01, f"读取 Java {package.major} 运行时清单")
    manifest_payload = net.fetch_bytes(package.manifest_url, cancel=cancel, attempts=3)
    if package.manifest_sha1 and _sha1(manifest_payload) != package.manifest_sha1.lower():
        raise DownloadError("Java manifest SHA-1 校验失败")
    try:
        manifest = json.loads(manifest_payload.decode("utf-8"))
        files = manifest["files"]
    except (UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise DownloadError(f"Java manifest 格式不正确：{exc}") from exc
    if not isinstance(files, dict):
        raise DownloadError("Java manifest 缺少文件列表")

    temp_dir = target_root / f".java-{package.major}.new-{uuid.uuid4().hex[:8]}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True)

    jobs: list[tuple[str, dict, dict]] = []
    for relative, meta in files.items():
        if not isinstance(meta, dict):
            continue
        kind = meta.get("type")
        destination = (temp_dir / relative).resolve(strict=False)
        try:
            destination.relative_to(temp_dir.resolve(strict=False))
        except ValueError:
            raise DownloadError(f"Java manifest 包含不安全路径：{relative}")
        if kind == "directory":
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if kind != "file":
            continue
        downloads = meta.get("downloads") if isinstance(meta.get("downloads"), dict) else {}
        compressed = downloads.get("lzma")
        raw = downloads.get("raw")
        if not isinstance(compressed, dict) or not compressed.get("url"):
            if not isinstance(raw, dict) or not raw.get("url"):
                raise DownloadError(f"Java 文件缺少下载地址：{relative}")
            compressed = raw
        jobs.append((relative, meta, compressed))

    total_bytes = sum(int(job[2].get("size") or 0) for job in jobs)
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
            if total_bytes:
                fraction = min(1.0, done_bytes / total_bytes)
            elif jobs:
                fraction = min(1.0, done_files / len(jobs))
            else:
                fraction = 1.0
            message = current[0] or f"Java 文件 {done_files}/{len(jobs)}"
        try:
            progress(0.03 + fraction * 0.94, message)
        except Exception:
            pass

    work: queue.Queue[tuple[str, dict, dict]] = queue.Queue()
    for job in jobs:
        work.put(job)

    def worker() -> None:
        nonlocal done_bytes, done_files
        while not cancel.is_set():
            try:
                relative, meta, download = work.get_nowait()
            except queue.Empty:
                return
            with lock:
                current[0] = f"Java 文件 · {relative}"
            emit(force=True)
            compressed_path = temp_dir / (relative + ".lzma")
            compressed_path.parent.mkdir(parents=True, exist_ok=True)
            destination = temp_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            item = DownloadItem(
                url=str(download.get("url")),
                dest=compressed_path,
                sha1=download.get("sha1"),
                size=_as_int(download.get("size")),
                label=f"Java · {relative}",
                kind="java",
            )
            try:
                net.download_item(
                    item,
                    cancel=cancel,
                    chunk_callback=lambda amount: _chunk(amount),
                    attempts=3,
                )
                payload = compressed_path.read_bytes()
                downloads_meta = (
                    meta.get("downloads")
                    if isinstance(meta.get("downloads"), dict)
                    else {}
                )
                raw = downloads_meta.get("raw")
                if isinstance(raw, dict) and download is not raw:
                    try:
                        payload = lzma.decompress(payload)
                    except lzma.LZMAError as exc:
                        raise DownloadError(f"Java 文件解压失败：{relative}") from exc
                expected_size = _as_int((raw or {}).get("size"))
                if expected_size is not None and len(payload) != expected_size:
                    raise DownloadError(f"Java 文件大小不符：{relative}")
                expected_sha1 = str((raw or {}).get("sha1") or "")
                if expected_sha1 and _sha1(payload) != expected_sha1.lower():
                    raise DownloadError(f"Java 文件 SHA-1 校验失败：{relative}")
                destination.write_bytes(payload)
                try:
                    compressed_path.unlink()
                except OSError:
                    pass
            except DownloadCancelled:
                return
            except Exception as exc:
                with lock:
                    if len(failures) < 100:
                        failures.append(str(exc))
            else:
                with lock:
                    done_files += 1
                emit()

    def _chunk(amount: int) -> None:
        nonlocal done_bytes
        with lock:
            done_bytes += amount
        emit()

    threads = [
        threading.Thread(target=worker, name=f"mcvd-java-{i}", daemon=True)
        for i in range(min(max(1, workers), max(1, len(jobs))))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        while thread.is_alive():
            thread.join(timeout=0.25)
    if cancel.is_set():
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise DownloadCancelled()
    if failures:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise DownloadError(f"{len(failures)} 个 Java 文件失败：{failures[0]}")

    progress(0.98, "安装 Java 运行时")
    java_candidates = list(temp_dir.rglob("bin/java.exe"))
    if not java_candidates:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise DownloadError("Java 运行时中没有找到 bin/java.exe")
    if final_dir.exists():
        shutil.rmtree(final_dir)
    try:
        temp_dir.replace(final_dir)
    except OSError:
        shutil.move(str(temp_dir), str(final_dir))
    java_exe = next(final_dir.rglob("bin/java.exe"))
    metadata = {
        "format": 1,
        "app": APP_VERSION,
        "major": package.major,
        "component": package.component,
        "version": package.version,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "java_exe": str(java_exe),
    }
    (target_root / f"java-{package.major}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    progress(1.0, f"Java {package.major} 已安装")
    return JavaInstall(
        major=package.major,
        image_type=package.image_type,
        version=package.version,
        directory=final_dir,
        java_exe=java_exe,
        archived_size=package.total_size,
    )


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _java_major_of(java_exe: Path) -> int | None:
    creationflags = 0x08000000 if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [str(java_exe), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=12,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = completed.stdout.decode("utf-8", "replace")
    match = re.search(r'version\s+"(?:1\.)?(\d+)', text)
    if not match:
        return None
    return int(match.group(1))


def find_java(major: int) -> Path | None:
    """Find a locally installed Java with the requested major version."""
    candidates: list[Path] = []
    candidates.append(default_java_root() / f"java-{major}" / "bin" / "java.exe")
    home = os.environ.get("JAVA_HOME")
    if home:
        candidates.append(Path(home) / "bin" / "java.exe")
    which = shutil.which("java")
    if which:
        candidates.append(Path(which))
    roots = [
        Path("C:/Program Files/Java"),
        Path("C:/Program Files/Eclipse Adoptium"),
        Path("C:/Program Files/Microsoft"),
        Path("C:/Program Files/Zulu"),
        Path("C:/Program Files/BellSoft"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Eclipse Adoptium",
    ]
    patterns = (
        f"jdk-{major}*",
        f"jdk{major}*",
        f"jre-{major}*",
        f"jre{major}*",
        f"zulu-{major}*",
        f"LibericaJDK-{major}*",
    )
    for root in roots:
        try:
            for pattern in patterns:
                for directory in root.glob(pattern):
                    candidates.append(directory / "bin" / "java.exe")
        except OSError:
            continue
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve(strict=False)).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        if not candidate.is_file():
            continue
        if _java_major_of(candidate) == major:
            return candidate
    return None


def ensure_java(
    major: int,
    *,
    cancel: threading.Event | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Return a matching java.exe, downloading a Mojang runtime if needed."""
    cancel = cancel or threading.Event()
    progress = on_progress or (lambda _fraction, _message: None)
    status = on_status or (lambda _message: None)
    found = find_java(major)
    if found is not None:
        status(f"使用本机 Java {major}：{found}")
        return found
    status(f"未找到 Java {major}，准备自动下载")
    progress(0.01, f"读取 Java {major} 运行时信息")
    package = fetch_java_package(major, cancel=cancel)
    install = download_java(
        package,
        default_java_root(),
        cancel=cancel,
        on_progress=progress,
    )
    status(f"Java {major} 已就绪：{install.java_exe}")
    return install.java_exe
