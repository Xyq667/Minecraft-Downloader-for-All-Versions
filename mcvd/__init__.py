"""MC Version Downloader package."""

from .config import APP_NAME, APP_VERSION, APP_VERSION_DISPLAY

__version__ = APP_VERSION
__version_display__ = APP_VERSION_DISPLAY

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "APP_VERSION_DISPLAY",
    "__version__",
    "__version_display__",
]
