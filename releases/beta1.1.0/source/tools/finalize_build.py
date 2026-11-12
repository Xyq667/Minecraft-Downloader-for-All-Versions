"""Publish the built exe to the release directory on D: (plus local copies)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcvd import __version__  # noqa: E402

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
        print(f"release directory: {release_dir}")


if __name__ == "__main__":
    main()
