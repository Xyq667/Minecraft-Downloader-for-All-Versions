"""Discover Minecraft game directories used by common launchers."""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

EXCLUDED_DIR_NAMES = {
    "$recycle.bin",
    "system volume information",
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "appdata",
    "node_modules",
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    "packages",
    "windowsapps",
    "recovery",
    "perflogs",
    "msocache",
    "config.msi",
}

COMMON_FOLDER_NAMES = {
    "minecraft",
    "mc",
    ".minecraft",
    "games",
    "game",
    "launcher",
    "launchers",
    "我的世界",
    "我的游戏",
    "游戏",
    "启动器",
    "游戏目录",
}

LAUNCHER_TOKENS = (
    ("prismlauncher", "Prism Launcher"),
    ("polymc", "PolyMC"),
    ("multimc", "MultiMC"),
    ("bakaxl", "BakaXL"),
    ("hmcl", "HMCL"),
    ("pcl", "PCL2"),
    ("atlauncher", "ATLauncher"),
    ("gdlauncher", "GDLauncher"),
    ("curseforge", "CurseForge"),
    ("modrinth", "Modrinth App"),
    ("technic", "Technic Launcher"),
)


@dataclass
class LauncherDir:
    path: Path
    label: str
    version_count: int
    kind: str


def _norm(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve(strict=False)))
    except OSError:
        return os.path.normcase(str(path))


def _has_versions_dir(path: Path) -> bool:
    try:
        return (path / "versions").is_dir()
    except OSError:
        return False


def _count_versions(path: Path) -> int:
    try:
        with os.scandir(path / "versions") as entries:
            return sum(1 for entry in entries if entry.is_dir(follow_symlinks=False))
    except OSError:
        return 0


def _is_official_path(path: Path) -> bool:
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    localappdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    candidates = {
        appdata / ".minecraft",
        home / ".minecraft",
        localappdata / ".minecraft",
    }
    return any(_norm(path) == _norm(candidate) for candidate in candidates)


def _label_for(path: Path, kind: str = "") -> str:
    lower = str(path).replace("\\", "/").lower()
    if kind == "official" or (kind == "game" and _is_official_path(path)):
        return "官方启动器"
    if kind == "instance":
        instance_name = ""
        parent = path.parent.name
        if parent and parent.lower() not in (".minecraft", "minecraft"):
            instance_name = parent
        base = "整合包实例"
        for token, label in LAUNCHER_TOKENS:
            if token in lower:
                base = label
                break
        return f"{base} · {instance_name}" if instance_name else base
    for token, label in LAUNCHER_TOKENS:
        if token in lower:
            return label
    if path.name.lower() == ".minecraft":
        return "Minecraft 目录"
    return "Minecraft 游戏目录"


def _drive_roots() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return [Path(f"{letter}:/") for letter in "CDEFGH"]
    roots: list[Path] = []
    for index in range(26):
        if not (bitmask & (1 << index)):
            continue
        root = Path(f"{chr(65 + index)}:/")
        try:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(str(root))
        except Exception:
            drive_type = 3
        if drive_type in (2, 3, 4):  # removable, fixed, remote
            roots.append(root)
    return roots


def _iter_child_dirs(path: Path):
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        yield Path(entry.path)
                except OSError:
                    continue
    except OSError:
        return


def _scan_breadth(
    roots: list[Path],
    depth: int,
    add,
    cancel: threading.Event | None,
    deadline: float,
) -> None:
    """Bounded breadth-first scan for directories that contain a versions folder."""
    frontier = list(roots)
    for current_depth in range(depth):
        if time.monotonic() > deadline:
            return
        next_frontier: list[Path] = []
        for directory in frontier:
            if cancel is not None and cancel.is_set():
                return
            if time.monotonic() > deadline:
                return
            if not directory.is_dir():
                continue
            if _has_versions_dir(directory):
                add(directory, "game")
            if current_depth >= depth - 1:
                continue
            for child in _iter_child_dirs(directory):
                if cancel is not None and cancel.is_set():
                    return
                if child.name.lower() in EXCLUDED_DIR_NAMES:
                    continue
                if child.name.startswith(".") and child.name.lower() != ".minecraft":
                    continue
                next_frontier.append(child)
        frontier = next_frontier
        if not frontier:
            return


def _standard_candidates() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ
    home = Path.home()
    appdata = Path(env.get("APPDATA", home / "AppData" / "Roaming"))
    localappdata = Path(env.get("LOCALAPPDATA", home / "AppData" / "Local"))
    userprofile = Path(env.get("USERPROFILE", home))
    candidates.extend(
        [
            appdata / ".minecraft",
            userprofile / ".minecraft",
            localappdata / ".minecraft",
        ]
    )
    for folder in (
        "Desktop",
        "Downloads",
        "Documents",
        "Games",
        "Game",
        "Minecraft",
        "我的游戏",
        "游戏",
    ):
        candidates.append(userprofile / folder)
    for drive in _drive_roots():
        for name in (
            ".minecraft",
            "Minecraft",
            "Minecraft/.minecraft",
            "Games",
            "Games/Minecraft",
            "Game",
            "MC",
            "我的世界",
            "我的游戏",
            "游戏",
            "启动器",
        ):
            candidates.append(drive / name)
    return candidates


def _instance_roots() -> list[Path]:
    env = os.environ
    home = Path.home()
    appdata = Path(env.get("APPDATA", home / "AppData" / "Roaming"))
    result: list[Path] = []
    bases = [
        appdata / "PrismLauncher" / "instances",
        appdata / "PolyMC" / "instances",
        appdata / "MultiMC" / "instances",
        appdata / "ATLauncher" / "instances",
        appdata / "gdlauncher_next" / "instances",
        appdata / "com.modrinth.theseus" / "profiles",
        home / "curseforge" / "minecraft" / "Instances",
        appdata / "CurseForge" / "minecraft" / "Instances",
    ]
    for base in bases:
        if not base.is_dir():
            continue
        for child in _iter_child_dirs(base):
            result.append(child)
    return result


def _official_profile_dirs() -> list[Path]:
    env = os.environ
    home = Path.home()
    appdata = Path(env.get("APPDATA", home / "AppData" / "Roaming"))
    result: list[Path] = []
    for base in (appdata / ".minecraft", home / ".minecraft"):
        profile_file = base / "launcher_profiles.json"
        if not profile_file.is_file():
            continue
        try:
            data = json.loads(profile_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        profiles = data.get("profiles") if isinstance(data, dict) else None
        if not isinstance(profiles, dict):
            continue
        for profile in profiles.values():
            if not isinstance(profile, dict):
                continue
            game_dir = profile.get("gameDir")
            if game_dir:
                result.append(Path(str(game_dir)))
    return result


def detect_launchers(
    cancel: threading.Event | None = None,
    *,
    deep: bool = True,
    timeout: float = 6.0,
) -> list[LauncherDir]:
    """Find game roots that already have a ``versions`` directory."""
    deadline = time.monotonic() + timeout
    found: dict[str, LauncherDir] = {}

    def add(path: Path, kind: str = "game") -> None:
        try:
            if cancel is not None and cancel.is_set():
                return
            path = path.expanduser()
            if not path.is_absolute():
                return
            if path.name.lower() == "versions":
                path = path.parent
            if not _has_versions_dir(path):
                return
            key = _norm(path)
            if key in found:
                return
            count = _count_versions(path)
            label = _label_for(path, kind)
            found[key] = LauncherDir(
                path=path.resolve(strict=False),
                label=label,
                version_count=count,
                kind=kind,
            )
        except (OSError, ValueError):
            return

    # Standard locations and explicit launcher instance layouts first.
    for candidate in _standard_candidates():
        add(candidate)
    for instance in _instance_roots():
        add(instance, "instance")
        add(instance / ".minecraft", "instance")
        add(instance / "minecraft", "instance")
    for profile_dir in _official_profile_dirs():
        add(profile_dir, "game")

    # Common launcher data roots, plus a bounded walk of non-system drives.
    roots: list[Path] = []
    env = os.environ
    home = Path.home()
    appdata = Path(env.get("APPDATA", home / "AppData" / "Roaming"))
    localappdata = Path(env.get("LOCALAPPDATA", home / "AppData" / "Local"))
    for base in (appdata, localappdata, home / "Desktop", home / "Downloads"):
        for child in _iter_child_dirs(base):
            lowered = child.name.lower()
            if any(token in lowered for token in ("hmcl", "pcl", "bakaxl", "prism", "multi", "mc")):
                roots.append(child)
                for nested in _iter_child_dirs(child):
                    roots.append(nested)

    if deep:
        for drive in _drive_roots():
            roots.append(drive)
            if str(drive).lower().startswith(("c:", "d:", "e:", "f:")):
                for child in _iter_child_dirs(drive):
                    if child.name.lower() in EXCLUDED_DIR_NAMES:
                        continue
                    if child.name.lower() in COMMON_FOLDER_NAMES:
                        roots.append(child)
    _scan_breadth(roots, 2, add, cancel, deadline)

    result = list(found.values())
    result.sort(key=lambda item: (-item.version_count, item.label, str(item.path)))
    return result
