"""Create a complete release zip (source + executable + docs)."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcvd import __version__  # noqa: E402


def _files() -> list[Path]:
    fixed = [
        ROOT / ".gitignore",
        ROOT / "LICENSE",
        ROOT / "README.md",
        ROOT / "GITHUB_REPO.md",
        ROOT / "app.py",
        ROOT / "build.ps1",
        ROOT / "requirements-build.txt",
        ROOT / "MC全版本下载器.exe",
        ROOT / "build" / "version_info.txt",
        ROOT / ".github" / "workflows" / "build.yml",
    ]
    folders = (
        ("mcvd", "*.py"),
        ("assets", "*"),
        ("docs", "*.png"),
        ("tests", "*.py"),
        ("tools", "*.py"),
    )
    for folder, pattern in folders:
        fixed.extend(sorted((ROOT / folder).glob(pattern)))
    return [path for path in fixed if path.is_file()]


def create_all_archive() -> Path:
    version = __version__
    destination = ROOT / f"MCVersionDownloader-{version}-all.zip"
    prefix = f"MCVersionDownloader-{version}"
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in _files():
            archive.write(path, f"{prefix}/{path.relative_to(ROOT).as_posix()}")
        archive.writestr(f"{prefix}/VERSION.txt", f"{version}\n")
    return destination


if __name__ == "__main__":
    output = create_all_archive()
    print(f"wrote {output} ({output.stat().st_size / 1024 / 1024:.1f} MB)")
