from __future__ import annotations

import gzip
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class RemoteFetchError(RuntimeError):
    pass


def _retry_count() -> int:
    raw = os.environ.get("METABODRIN_REMOTE_RETRIES", "").strip()
    if not raw:
        return 4
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


def _retry_backoff_seconds() -> float:
    raw = os.environ.get("METABODRIN_REMOTE_BACKOFF_SECONDS", "").strip()
    if not raw:
        return 1.5
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.5


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError)):
        return True
    return False


def _request(url: str, timeout: int = 60, retries: int | None = None, backoff_seconds: float | None = None) -> bytes:
    attempts = retries if retries is not None else _retry_count()
    base_backoff = backoff_seconds if backoff_seconds is not None else _retry_backoff_seconds()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MERIT/0.1 (+https://github.com/idtlab/AIDRIN inspired)"
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError) as exc:
            last_error = exc
            if not _is_retryable(exc) or attempt >= attempts:
                break
            sleep_seconds = base_backoff * (2 ** (attempt - 1))
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    raise RemoteFetchError(
        f"Remote fetch failed for {url} after {attempts} attempt(s): {last_error}"
    ) from last_error


def fetch_text(
    url: str,
    timeout: int = 60,
    retries: int | None = None,
    backoff_seconds: float | None = None,
) -> str:
    return _request(
        url,
        timeout=timeout,
        retries=retries,
        backoff_seconds=backoff_seconds,
    ).decode("utf-8")


def fetch_json(
    url: str,
    timeout: int = 60,
    retries: int | None = None,
    backoff_seconds: float | None = None,
) -> Any:
    return json.loads(
        fetch_text(
            url,
            timeout=timeout,
            retries=retries,
            backoff_seconds=backoff_seconds,
        )
    )


def download_text(url: str, destination: str | Path, timeout: int = 60) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(fetch_text(url, timeout=timeout))
    return destination


def download_binary(url: str, destination: str | Path, timeout: int = 60) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_request(url, timeout=timeout))
    return destination


def download_gzip_text(url: str, destination: str | Path, timeout: int = 60) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = fetch_text(url, timeout=timeout).encode("utf-8")
    with gzip.open(destination, "wb") as handle:
        handle.write(payload)
    return destination


def quote_path_component(value: str) -> str:
    return urllib.parse.quote(value, safe="")
