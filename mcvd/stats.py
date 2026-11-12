"""Persistent download-time statistics.

The file is intentionally human readable and small: cumulative counters plus
the most recent download sessions.  Every release gets its own application
data directory, so :func:`mcvd.config.ensure_app_data_dir` carries this file
forward when a new version is started for the first time.
"""

from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import app_data_dir

STATS_FILENAME = "download_stats.json"
SCHEMA_VERSION = 1
MAX_RECENT = 40
MAX_VERSION_IDS = 200

_LOCK = threading.Lock()


def stats_path() -> Path:
    return app_data_dir() / STATS_FILENAME


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(number):
        return default
    return max(0.0, number)


def _empty_stats() -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "total_runs": 0,
        "total_seconds": 0.0,
        "total_bytes": 0,
        "total_requested": 0,
        "total_installed": 0,
        "total_skipped": 0,
        "total_failed": 0,
        "total_cancelled": 0,
        "first_at": "",
        "last_at": "",
        "versions": {},
        "recent": [],
    }


def _normalize_timing(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    timing: dict[str, float] = {}
    for key, value in list(raw.items())[:MAX_VERSION_IDS]:
        if not isinstance(key, str) or not key:
            continue
        seconds = _as_float(value)
        if seconds > 0:
            timing[key] = round(seconds, 3)
    return timing


def _normalize_record(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    versions = raw.get("versions")
    if not isinstance(versions, list):
        versions = []
    clean_versions = [
        str(item)[:120]
        for item in versions[:MAX_VERSION_IDS]
        if isinstance(item, str) and item
    ]
    elapsed = _as_float(raw.get("elapsed"))
    size = _as_int(raw.get("bytes"))
    speed = _as_float(raw.get("average_speed"))
    if speed <= 0 and elapsed > 0 and size > 0:
        speed = size / elapsed
    return {
        "at": str(raw.get("at") or ""),
        "elapsed": round(elapsed, 3),
        "bytes": size,
        "requested": _as_int(raw.get("requested"), len(clean_versions)),
        "installed": _as_int(raw.get("installed")),
        "skipped": _as_int(raw.get("skipped")),
        "failed": _as_int(raw.get("failed")),
        "cancelled": bool(raw.get("cancelled")),
        "mode": "full" if raw.get("mode") == "full" else "core",
        "target": str(raw.get("target") or ""),
        "average_speed": round(speed, 3),
        "versions": clean_versions,
        "version_times": _normalize_timing(raw.get("version_times")),
    }


def _normalize_stats(raw: Any) -> dict[str, Any]:
    data = _empty_stats()
    if not isinstance(raw, dict):
        return data

    for key in (
        "total_runs",
        "total_bytes",
        "total_requested",
        "total_installed",
        "total_skipped",
        "total_failed",
        "total_cancelled",
    ):
        data[key] = _as_int(raw.get(key))
    data["total_seconds"] = round(_as_float(raw.get("total_seconds")), 3)
    data["first_at"] = str(raw.get("first_at") or "")
    data["last_at"] = str(raw.get("last_at") or "")

    versions = raw.get("versions")
    if isinstance(versions, dict):
        clean_versions: dict[str, dict[str, Any]] = {}
        for key, value in list(versions.items())[:MAX_VERSION_IDS]:
            if not isinstance(key, str) or not key or not isinstance(value, dict):
                continue
            seconds = _as_float(value.get("seconds"))
            runs = _as_int(value.get("runs"))
            size = _as_int(value.get("bytes"))
            if seconds <= 0 and runs <= 0:
                continue
            clean_versions[key] = {
                "seconds": round(seconds, 3),
                "runs": runs,
                "bytes": size,
            }
        data["versions"] = clean_versions

    recent = raw.get("recent")
    if isinstance(recent, list):
        records = [_normalize_record(item) for item in recent[:MAX_RECENT]]
        data["recent"] = [item for item in records if item is not None]

    # Repair counters from the retained history when the file was truncated
    # or hand-edited.  Totals are not derived from recent records because the
    # history is intentionally capped.
    if data["recent"] and not data["last_at"]:
        data["last_at"] = str(data["recent"][0].get("at") or "")
    if data["recent"] and not data["first_at"]:
        data["first_at"] = str(data["recent"][-1].get("at") or "")
    return data


def _load_unlocked(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_stats()
    return _normalize_stats(raw)


def load_stats(path: Path | None = None) -> dict[str, Any]:
    target = path or stats_path()
    try:
        return _load_unlocked(target)
    except OSError:
        return _empty_stats()


def _save_unlocked(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def save_stats(data: dict[str, Any], path: Path | None = None) -> None:
    target = path or stats_path()
    with _LOCK:
        _save_unlocked(_normalize_stats(data), target)


def record_download(
    result: Any,
    game_dir: Path | str,
    mode: str,
    *,
    version_ids: list[str] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Append one finished download session and update cumulative totals.

    ``result`` is an :class:`mcvd.core.EngineResult`.  Keeping this function
    duck-typed avoids a circular import and makes it easy to test with a
    lightweight stand-in object.
    """

    target = path or stats_path()
    elapsed = _as_float(getattr(result, "elapsed", 0.0))
    size = _as_int(getattr(result, "session_bytes", 0))
    installed = _as_int(getattr(result, "installed", 0))
    skipped = _as_int(getattr(result, "skipped", 0))
    failed = _as_int(getattr(result, "failed", 0))
    cancelled = bool(getattr(result, "cancelled", False))
    requested = _as_int(getattr(result, "requested", 0))

    timing = _normalize_timing(getattr(result, "version_elapsed", {}))
    ids = version_ids
    if ids is None:
        ids = getattr(result, "version_ids", None)
    if not isinstance(ids, list):
        ids = list(timing)
    clean_ids = [
        str(item)[:120] for item in ids[:MAX_VERSION_IDS] if isinstance(item, str) and item
    ]
    requested = max(requested, len(clean_ids))
    speed = size / elapsed if elapsed > 0 and size > 0 else 0.0
    at = datetime.now().astimezone().isoformat(timespec="seconds")

    record = {
        "at": at,
        "elapsed": round(elapsed, 3),
        "bytes": size,
        "requested": requested,
        "installed": installed,
        "skipped": skipped,
        "failed": failed,
        "cancelled": cancelled,
        "mode": "full" if mode == "full" else "core",
        "target": str(game_dir),
        "average_speed": round(speed, 3),
        "versions": clean_ids,
        "version_times": timing,
    }

    with _LOCK:
        data = _load_unlocked(target)
        data["total_runs"] += 1
        data["total_seconds"] = round(data["total_seconds"] + elapsed, 3)
        data["total_bytes"] += size
        data["total_requested"] += requested
        data["total_installed"] += installed
        data["total_skipped"] += skipped
        data["total_failed"] += failed
        data["total_cancelled"] += int(cancelled)
        if not data["first_at"]:
            data["first_at"] = at
        data["last_at"] = at

        for version_id, seconds in timing.items():
            item = data["versions"].setdefault(
                version_id, {"seconds": 0.0, "runs": 0, "bytes": 0}
            )
            item["seconds"] = round(_as_float(item.get("seconds")) + seconds, 3)
            item["runs"] = _as_int(item.get("runs")) + 1

        data["recent"].insert(0, record)
        del data["recent"][MAX_RECENT:]
        _save_unlocked(data, target)
    return data


def clear_stats(path: Path | None = None) -> None:
    target = path or stats_path()
    with _LOCK:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass


def average_speed(data: dict[str, Any]) -> float:
    seconds = _as_float(data.get("total_seconds"))
    size = _as_int(data.get("total_bytes"))
    return size / seconds if seconds > 0 and size > 0 else 0.0
