"""Publish the built exe to the release directory on D: (plus local copies)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcvd import __version__  # noqa: E402
from tools.archive_all import create_all_archive  # noqa: E402
from tools.archive_source import create_source_archive  # noqa: E402

DIST = ROOT / "dist"
SOURCE = DIST / "MCVersionDownloader.exe"
LOCAL_TARGET = DIST / "MC全版本下载器.exe"
ROOT_TARGET = ROOT / "MC全版本下载器.exe"


def mark_medium(path: Path) -> None:
    try:
        subprocess.run(
            ["icacls", str(path), "/setintegritylevel", "Medium"],
            capture_output=True,
            check=False,
        )
    except OSError:
        pass


def copy_exe(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, destination)
    mark_medium(destination)
    print(f"wrote {destination} ({destination.stat().st_size / 1024 / 1024:.1f} MB)")


def copy_docs(destination: Path) -> None:
    """Keep README screenshots working in portable release folders."""
    for name in ("preview.png", "stats.png", "loaders.png", "about.png", "agreement.png"):
        source = ROOT / "docs" / name
        if not source.is_file():
            continue
        target = destination / "docs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"missing build artifact: {SOURCE}")

    copy_exe(LOCAL_TARGET)
    copy_exe(ROOT_TARGET)

    # A fresh folder for every release; old release folders stay untouched.
    if Path("D:/").exists():
        release_dir = Path("D:/") / "MCVersionDownloader" / __version__
        copy_exe(release_dir / "MC全版本下载器.exe")
        readme = ROOT / "README.md"
        if readme.is_file():
            shutil.copy2(readme, release_dir / "README.md")
        copy_docs(release_dir)
        print(f"release directory: {release_dir}")

    # Keep the user's merged project directory up to date as well.
    merged_root = Path("D:/xyq/mc-version-downloaderbeta1.0.0")
    if merged_root.is_dir():
        copy_exe(merged_root / "MC全版本下载器.exe")
        readme = ROOT / "README.md"
        if readme.is_file() and merged_root.resolve() != ROOT.resolve():
            shutil.copy2(readme, merged_root / "README.md")
        archive = merged_root / "releases" / __version__
        copy_exe(archive / "MC全版本下载器.exe")
        if readme.is_file():
            shutil.copy2(readme, archive / "README.md")
        copy_docs(archive)
        print(f"merged project: {merged_root}")

    source_archive = create_source_archive()
    print(f"source archive: {source_archive}")
    all_archive = create_all_archive()
    print(f"all-in-one archive: {all_archive}")


if __name__ == "__main__":
    main()
