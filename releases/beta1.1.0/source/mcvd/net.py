"""Small, dependency-free HTTP download primitives with checksum verification."""

from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import USER_AGENT

CHUNK_SIZE = 256 * 1024
DEFAULT_TIMEOUT = 35
DEFAULT_ATTEMPTS = 3
KEEPALIVE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}
_SSL_CONTEXT = ssl.create_default_context()
_TLS = threading.local()
_PROXY_MODE: bool | None = None


class DownloadCancelled(Exception):
    """Raised when the user cancels an in-flight download."""


class DownloadError(Exception):
    """A human-readable download failure."""


class _StatusError(DownloadError):
    def __init__(self, code: int, reason: str, url: str):
        super().__init__(f"HTTP {code} {reason}".strip())
        self.code = code
        self.reason = reason
        self.url = url


@dataclass(frozen=True)
class DownloadItem:
    url: str
    dest: Path
    sha1: str | None = None
    size: int | None = None
    label: str = ""
    kind: str = "file"

    @property
    def name(self) -> str:
        return self.label or self.dest.name


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled()


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DownloadError(f"非法的下载地址：{url[:120]}")


def _request(url: str) -> urllib.request.Request:
    _validate_url(url)
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
        },
    )


def http_open(url: str, timeout: float = DEFAULT_TIMEOUT):
    return urllib.request.urlopen(_request(url), timeout=timeout)


def _can_pool() -> bool:
    """Keep-alive is unsafe/unsupported through some configured proxies."""
    global _PROXY_MODE
    if _PROXY_MODE is None:
        try:
            proxies = urllib.request.getproxies()
        except Exception:
            proxies = {}
        _PROXY_MODE = not any(
            isinstance(value, str) and value
            for key, value in proxies.items()
            if key in ("http", "https", "all")
        )
    return _PROXY_MODE


def _connections() -> dict[tuple[str, str, int], http.client.HTTPConnection]:
    pool = getattr(_TLS, "connections", None)
    if pool is None:
        pool = {}
        _TLS.connections = pool
    return pool


def _drop_connection(key: tuple[str, str, int]) -> None:
    connection = _connections().pop(key, None)
    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass


def _new_connection(
    scheme: str, host: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    if scheme == "https":
        return http.client.HTTPSConnection(
            host, port, timeout=timeout, context=_SSL_CONTEXT
        )
    return http.client.HTTPConnection(host, port, timeout=timeout)


class _PooledResponse:
    def __init__(
        self,
        response: http.client.HTTPResponse,
        key: tuple[str, str, int],
    ):
        self.response = response
        self.key = key

    @property
    def headers(self):
        return self.response.headers

    @property
    def status(self) -> int:
        return self.response.status

    def read(self, amount: int = -1) -> bytes:
        return self.response.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.response.close()
        finally:
            if exc_type is not None or getattr(self.response, "will_close", False):
                _drop_connection(self.key)


def _pooled_open(url: str, timeout: float) -> _PooledResponse:
    current = url
    for _redirect in range(6):
        _validate_url(current)
        parsed = urllib.parse.urlsplit(current)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme == "https" else 80)
        key = (scheme, host, port)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection = _connections().get(key)
        if connection is None:
            connection = _new_connection(scheme, host, port, timeout)
            _connections()[key] = connection
        else:
            connection.timeout = timeout
        try:
            connection.request(
                "GET",
                path,
                headers=KEEPALIVE_HEADERS,
            )
            response = connection.getresponse()
        except (http.client.HTTPException, OSError, ssl.SSLError):
            _drop_connection(key)
            raise
        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location")
            response.read()
            if getattr(response, "will_close", False):
                _drop_connection(key)
            if not location:
                raise DownloadError(f"HTTP {response.status} 缺少跳转地址")
            current = urllib.parse.urljoin(current, location)
            continue
        if response.status >= 400:
            response.read(4096)
            if getattr(response, "will_close", False):
                _drop_connection(key)
            raise _StatusError(response.status, response.reason or "", current)
        return _PooledResponse(response, key)
    raise DownloadError("重定向次数过多")


class _UrllibStream:
    def __init__(self, response):
        self.response = response

    @property
    def headers(self):
        return self.response.headers

    def read(self, amount: int = -1) -> bytes:
        return self.response.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.response.close()


def _open_stream(url: str, timeout: float):
    if _can_pool():
        return _pooled_open(url, timeout)
    return _UrllibStream(http_open(url, timeout=timeout))


def fetch_bytes(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    cancel: threading.Event | None = None,
    max_bytes: int = 32 * 1024 * 1024,
) -> bytes:
    """Fetch a small resource (manifest or index) into memory."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        _check_cancel(cancel)
        try:
            with http_open(url, timeout=timeout) as response:
                data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise DownloadError("服务器返回的数据异常地大")
            return data
        except DownloadCancelled:
            raise
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (400, 401, 403, 404, 410):
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
        if attempt < attempts:
            _sleep_cancellable(0.7 * attempt, cancel)
    raise DownloadError(_describe_error(last_error))


def _sleep_cancellable(seconds: float, cancel: threading.Event | None) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _check_cancel(cancel)
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _describe_error(error: Exception | None) -> str:
    if error is None:
        return "未知网络错误"
    if isinstance(error, _StatusError):
        return str(error)
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} {error.reason}".strip()
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, OSError) and getattr(reason, "winerror", None):
            return f"网络连接失败（Windows 错误 {reason.winerror}）"
        return f"网络连接失败：{reason}"
    if isinstance(error, (TimeoutError, http.client.HTTPException)):
        return f"网络连接中断：{error}" if str(error) else "网络连接中断"
    return str(error) or error.__class__.__name__


def sha1_file(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while True:
            _check_cancel(cancel)
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def file_is_usable(path: Path, size: int | None, sha1: str | None = None) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    if not path.is_file() or stat.st_size <= 0:
        return False
    if size is not None and stat.st_size != size:
        return False
    if sha1 and stat.st_size <= 1024 * 1024:
        try:
            return sha1_file(path).lower() == sha1.lower()
        except OSError:
            return False
    return True


def download_item(
    item: DownloadItem,
    *,
    cancel: threading.Event | None = None,
    chunk_callback: Callable[[int], None] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    force: bool = False,
    verify_existing_hash: bool = False,
) -> int:
    """Download one file atomically.

    Returns the number of bytes read from the network. Existing files that match
    the expected size (and optionally checksum) are kept untouched.
    """
    dest = item.dest
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not force and dest.is_file():
        if verify_existing_hash and item.sha1:
            try:
                if sha1_file(dest, cancel).lower() == item.sha1.lower():
                    return 0
            except OSError:
                pass
        elif file_is_usable(dest, item.size):
            return 0

    part = dest.with_name(dest.name + ".part")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        _check_cancel(cancel)
        try:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass
            received = 0
            digest = hashlib.sha1()
            with _open_stream(item.url, timeout=timeout) as response:
                expected = item.size
                header_length = response.headers.get("Content-Length")
                if expected is None and header_length and header_length.isdigit():
                    expected = int(header_length)
                with part.open("wb") as handle:
                    while True:
                        _check_cancel(cancel)
                        block = response.read(CHUNK_SIZE)
                        if not block:
                            break
                        handle.write(block)
                        digest.update(block)
                        received += len(block)
                        if chunk_callback is not None:
                            chunk_callback(len(block))
                handle_size = part.stat().st_size
            if item.size is not None and handle_size != item.size:
                raise DownloadError(
                    f"文件大小不符（应为 {item.size} 字节，实际 {handle_size} 字节）"
                )
            if item.sha1 and digest.hexdigest().lower() != item.sha1.lower():
                raise DownloadError("SHA-1 校验失败，文件可能损坏")
            os.replace(part, dest)
            return received
        except DownloadCancelled:
            _remove_quietly(part)
            raise
        except urllib.error.HTTPError as exc:
            _remove_quietly(part)
            last_error = exc
            if exc.code in (400, 401, 403, 404, 410):
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            _remove_quietly(part)
            last_error = exc
        except DownloadError as exc:
            _remove_quietly(part)
            last_error = exc
            if isinstance(exc, _StatusError) and exc.code in (400, 401, 403, 404, 410):
                break
        if attempt < attempts:
            _sleep_cancellable(0.8 * attempt, cancel)
    detail = _describe_error(last_error)
    raise DownloadError(f"{item.name}：{detail}")


def _remove_quietly(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def copy_or_link(src: Path, dest: Path) -> None:
    """Create a hard link when possible, otherwise copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        try:
            if dest.stat().st_size == src.stat().st_size:
                return
        except OSError:
            pass
        try:
            dest.unlink()
        except OSError:
            pass
    try:
        os.link(src, dest)
        return
    except (OSError, NotImplementedError):
        pass
    shutil.copy2(src, dest)
