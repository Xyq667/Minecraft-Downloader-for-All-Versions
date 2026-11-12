"""Shared configuration, paths, settings and small UI-agnostic helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

APP_NAME = "MC 全版本下载器"
APP_NAME_EN = "MC Version Downloader"
APP_ID = "MCVersionDownloader"
APP_VERSION = "v1.1.0"
APP_VERSION_DISPLAY = "v1.1.0"
USER_AGENT = f"{APP_NAME_EN}/{APP_VERSION} (+https://www.minecraft.net/)"
AUTHOR = "Xyq_yy"
GITHUB_URL = "https://github.com/Xyq667/Minecraft-Downloader-for-All-Versions"
BILIBILI_URL = "https://space.bilibili.com/1364232155"
DEEPSEEK_CREDIT = "本程序由 DeepSeek 辅助完成。"
LICENSE_NAME = "MIT License"
LICENSE_TEXT = """MIT License

Copyright (c) 2026 Xyq_yy

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

MANIFEST_URLS = (
    "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
    "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
)

TYPE_LABELS = {
    "release": "正式版",
    "snapshot": "快照",
    "old_beta": "旧版 Beta",
    "old_alpha": "旧版 Alpha",
}
TYPE_ORDER = {"release": 0, "snapshot": 1, "old_beta": 2, "old_alpha": 3}

# Palette used by the GUI and the generated icon.
COLORS = {
    "bg": "#0b0f14",
    "bg_alt": "#0d131b",
    "card": "#111823",
    "card_alt": "#151d27",
    "border": "#222d3a",
    "border_soft": "#1a2430",
    "text": "#e8eef6",
    "text_soft": "#c5d0dd",
    "muted": "#8492a5",
    "muted_dark": "#617086",
    "accent": "#46df87",
    "accent_hover": "#69eba0",
    "accent_dark": "#2cbf6b",
    "accent_soft": "#173426",
    "blue": "#76a9ff",
    "warning": "#f0b35b",
    "danger": "#ff7777",
    "danger_soft": "#341d22",
    "black": "#06130b",
}

FONT_FAMILY = "Microsoft YaHei UI"

_APP_DATA_ROOT: Path | None = None


def _font_available(family: str) -> bool:
    """Best-effort check that a font family exists (Tk is optional here)."""
    try:
        import tkinter as tk
        import tkinter.font as tkfont

        root = tk._default_root
        if root is None:
            root = tk.Tk()
            root.withdraw()
            owns = True
        else:
            owns = False
        try:
            return family in tkfont.families(root)
        finally:
            if owns:
                root.destroy()
    except Exception:
        return True


def font_family() -> str:
    if sys.platform == "win32" and _font_available(FONT_FAMILY):
        return FONT_FAMILY
    return "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"


def _local_app_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / APP_ID


def app_data_root() -> Path:
    """Keep all application data on D: when that drive is writable."""
    global _APP_DATA_ROOT
    if _APP_DATA_ROOT is not None:
        return _APP_DATA_ROOT
    candidates: list[Path] = []
    if os.name == "nt" and Path("D:/").exists():
        candidates.append(Path("D:/") / APP_ID)
    candidates.append(_local_app_root())
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            _APP_DATA_ROOT = root
            return root
        except OSError:
            continue
    fallback = Path.home() / f".{APP_ID.lower()}"
    fallback.mkdir(parents=True, exist_ok=True)
    _APP_DATA_ROOT = fallback
    return fallback


def app_data_dir() -> Path:
    """Each software release gets its own data directory.

    New versions never reuse the previous release's directory. Existing
    settings/cache are copied forward by ensure_app_data_dir().
    """
    path = app_data_root() / APP_VERSION
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path.home() / f".{APP_ID.lower()}" / APP_VERSION
        path.mkdir(parents=True, exist_ok=True)
    return path


def _migration_source(target: Path) -> Path | None:
    """Find the most recent older data directory worth carrying forward."""
    candidates: list[Path] = []
    root = target.parent
    # Unversioned data written by beta1.0.0 or earlier, either on D: or in
    # %LOCALAPPDATA%.
    for old in (root, _local_app_root()):
        try:
            if old != target and old.is_dir() and any(
                (old / name).exists()
                for name in ("settings.json", "download_stats.json", "cache", "logs")
            ):
                candidates.append(old)
        except OSError:
            continue
    try:
        for child in root.iterdir():
            if not child.is_dir() or child == target:
                continue
            if any(
                (child / name).exists()
                for name in ("settings.json", "download_stats.json", "cache", "logs")
            ):
                candidates.append(child)
    except OSError:
        pass

    if not candidates:
        return None

    def freshness(path: Path) -> float:
        stamps: list[float] = []
        for name in (
            "settings.json",
            "download_stats.json",
            "version.json",
            "cache",
            "logs",
        ):
            try:
                stamps.append((path / name).stat().st_mtime)
            except OSError:
                continue
        return max(stamps, default=0.0)

    candidates.sort(key=freshness, reverse=True)
    return candidates[0]


def _copy_migration(source: Path, target: Path) -> None:
    """Copy small state files without overwriting data already in the target."""
    for name in ("settings.json", "download_stats.json"):
        src = source / name
        dest = target / name
        if src.is_file() and not dest.exists():
            try:
                shutil.copy2(src, dest)
            except OSError:
                pass
    for directory in ("cache", "logs"):
        src_root = source / directory
        if not src_root.is_dir():
            continue
        for src in src_root.rglob("*"):
            try:
                if not src.is_file():
                    continue
                rel = src.relative_to(src_root)
                dest = target / directory / rel
                if dest.exists():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            except OSError:
                continue


def ensure_app_data_dir() -> Path:
    """Create the current release directory and migrate state once."""
    target = app_data_dir()
    marker = target / "version.json"
    if marker.is_file():
        return target
    source = _migration_source(target)
    if source is not None:
        _copy_migration(source, target)
    payload = {
        "version": APP_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "migrated_from": str(source) if source is not None else "",
    }
    try:
        marker.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    return target


def cache_dir() -> Path:
    path = app_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_path(relative: str) -> Path:
    """Resolve a bundled resource both in source and in a PyInstaller one-file app."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def default_game_dir() -> Path:
    """Default download location is always the D drive when it is available."""
    if os.name == "nt":
        d_root = Path("D:/")
        if d_root.exists():
            return d_root / "Minecraft"
    return Path.home() / "Minecraft"


def normalize_game_dir(value: str | os.PathLike[str]) -> Path:
    """Return an absolute game root; accept a selected ``versions`` folder too."""
    raw = os.path.expandvars(os.path.expanduser(str(value).strip().strip('"')))
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve(strict=False)
    if path.name.lower() == "versions":
        path = path.parent
    return path


def has_drive(path: Path | str) -> bool:
    text = str(path)
    match = re.match(r"^([A-Za-z]):", text)
    return bool(match) and Path(f"{match.group(1)}:/").exists()


def free_space_text(path: Path) -> str:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = os.statvfs(probe) if hasattr(os, "statvfs") else None
    except OSError:
        usage = None
    if usage is not None:
        free = usage.f_bavail * usage.f_frsize
    else:
        try:
            import shutil

            free = shutil.disk_usage(probe).free
        except OSError:
            return "空间未知"
    return f"可用 {format_bytes(free)}"


def format_bytes(value: float | int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if index == 0:
        return f"{int(size)} B"
    if size >= 100:
        return f"{size:.0f} {units[index]}"
    if size >= 10:
        return f"{size:.1f} {units[index]}"
    return f"{size:.2f} {units[index]}"


def format_speed(value: float | int | None) -> str:
    if not value or value <= 0:
        return "—"
    return f"{format_bytes(value)}/s"


def format_duration(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {seconds:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes:02d} 分"


def format_clock(seconds: float | int | None) -> str:
    """Short clock used by the live timer and history table."""
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def friendly_date(value: str | None) -> str:
    moment = parse_iso_datetime(value)
    if moment is None:
        return "—"
    return moment.astimezone().strftime("%Y-%m-%d")


def friendly_datetime(value: str | None, pattern: str = "%m-%d %H:%M") -> str:
    moment = parse_iso_datetime(value)
    if moment is None:
        return "—"
    return moment.astimezone().strftime(pattern)


@dataclass
class Settings:
    game_dir: str = ""
    mode: str = "full"
    skip_existing: bool = True
    geometry: str = ""
    loader: str = "none"
    agreement_accepted: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("core", "full"):
            self.mode = "full"
        if self.loader not in ("none", "fabric", "quilt", "forge", "neoforge"):
            self.loader = "none"
        self.agreement_accepted = bool(self.agreement_accepted)
        if not self.game_dir:
            self.game_dir = str(default_game_dir())

    @classmethod
    def load(cls) -> "Settings":
        path = app_data_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        allowed: dict[str, Any] = {
            key: raw[key]
            for key in (
                "game_dir",
                "mode",
                "skip_existing",
                "geometry",
                "loader",
                "agreement_accepted",
            )
            if key in raw
        }
        try:
            settings = cls(**allowed)
        except TypeError:
            return cls()
        if settings.game_dir:
            settings.game_dir = str(normalize_game_dir(settings.game_dir))
        return settings

    def save(self) -> None:
        path = app_data_dir() / "settings.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, path)
        except OSError:
            pass


def truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 5:
        return text[:limit]
    left = (limit - 3) // 2
    right = limit - 3 - left
    return text[:left] + "..." + text[-right:]


def safe_version_id(version_id: str) -> str:
    """Reject anything that could escape the versions directory."""
    if not version_id or any(ch in version_id for ch in ("/", "\\", ":", "\0")):
        raise ValueError("非法版本名称")
    if version_id in (".", ".."):
        raise ValueError("非法版本名称")
    return version_id
