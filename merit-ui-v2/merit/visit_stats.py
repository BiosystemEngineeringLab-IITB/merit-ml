from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_UMAMI_WEBSITE_ID = "e9fa298c-3199-4358-a0ec-8fa401f3eb10"
_DEFAULT_UMAMI_API_BASE = "https://api.umami.is/v1"
# Umami tracking went live with the public MERIT-ML site consent banner rollout.
_DEFAULT_STATS_START_UTC = datetime(2025, 6, 20, tzinfo=timezone.utc)
_CACHE: dict[str, Any] = {"fetched_at": 0.0, "payload": None}


def _stats_mode() -> str:
    raw = str(os.getenv("MERIT_VISIT_STATS_MODE", "auto") or "auto").strip().lower()
    if raw in {"mock", "demo", "off", "disabled", "live", "auto"}:
        return raw
    return "auto"


def _cache_ttl_seconds() -> int:
    try:
        return max(60, int(os.getenv("MERIT_VISIT_STATS_CACHE_SECONDS", "3600")))
    except ValueError:
        return 3600


def _stats_start_ms() -> int:
    configured = str(os.getenv("MERIT_VISIT_STATS_START_MS", "") or "").strip()
    if configured.isdigit():
        return int(configured)
    configured_date = str(os.getenv("MERIT_VISIT_STATS_START_DATE", "") or "").strip()
    if configured_date:
        try:
            dt = datetime.strptime(configured_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            pass
    return int(_DEFAULT_STATS_START_UTC.timestamp() * 1000)


def _period_label(start_ms: int) -> str:
    try:
        dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        return f"since {dt.strftime('%B %Y')}"
    except (OverflowError, OSError, ValueError):
        return "all time"


def _mock_payload() -> dict[str, Any]:
    visitors = int(os.getenv("MERIT_VISIT_STATS_MOCK_VISITORS", "1284") or "1284")
    pageviews = int(os.getenv("MERIT_VISIT_STATS_MOCK_PAGEVIEWS", "3567") or "3567")
    start_ms = _stats_start_ms()
    return {
        "available": True,
        "mock": True,
        "visitors": visitors,
        "pageviews": pageviews,
        "period_label": _period_label(start_ms),
        "source": "mock",
        "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "privacy_note": (
            "Demo numbers for local UI preview. Production uses anonymous aggregate totals "
            "from Umami Analytics; no personal data is collected or displayed."
        ),
    }


def _metric_value(payload: dict[str, Any], key: str) -> int:
    raw = payload.get(key)
    if isinstance(raw, dict):
        for field in ("value", "count", "total"):
            if field in raw:
                try:
                    return int(raw[field])
                except (TypeError, ValueError):
                    continue
        return 0
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _fetch_umami_stats() -> dict[str, Any]:
    api_key = str(os.getenv("UMAMI_API_KEY", "") or "").strip()
    if not api_key:
        return {"available": False, "reason": "missing_api_key"}

    website_id = str(os.getenv("UMAMI_WEBSITE_ID", _UMAMI_WEBSITE_ID) or _UMAMI_WEBSITE_ID).strip()
    api_base = str(os.getenv("UMAMI_API_BASE_URL", _DEFAULT_UMAMI_API_BASE) or _DEFAULT_UMAMI_API_BASE).rstrip("/")
    start_ms = _stats_start_ms()
    end_ms = int(time.time() * 1000)
    query = urlencode({"startAt": start_ms, "endAt": end_ms})
    url = f"{api_base}/websites/{website_id}/stats?{query}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "MERIT-ML/1.0 (+https://www.merit-ml.in)",
        "x-umami-api-key": api_key,
    }
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:240]
        except Exception:
            body = ""
        return {"available": False, "reason": f"umami_http_{exc.code}", "detail": body}
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": "umami_fetch_failed", "detail": str(exc)}

    visitors = _metric_value(payload, "visitors")
    pageviews = _metric_value(payload, "pageviews")
    return {
        "available": True,
        "mock": False,
        "visitors": visitors,
        "pageviews": pageviews,
        "period_label": _period_label(start_ms),
        "source": "umami",
        "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "privacy_note": (
            "Anonymous aggregate counts from Umami Analytics. MERIT-ML does not store IP "
            "addresses, accounts, or personal identifiers for this display. The site only loads "
            "Umami after you accept the analytics notice; declined visits are not counted."
        ),
    }


def get_public_visit_stats(*, force_refresh: bool = False) -> dict[str, Any]:
    mode = _stats_mode()
    if mode in {"off", "disabled"}:
        return {"available": False, "reason": "disabled"}

    now = time.time()
    if (
        not force_refresh
        and _CACHE.get("payload") is not None
        and (now - float(_CACHE.get("fetched_at") or 0.0)) < _cache_ttl_seconds()
    ):
        return dict(_CACHE["payload"])

    if mode in {"mock", "demo"}:
        payload = _mock_payload()
    elif mode == "live":
        payload = _fetch_umami_stats()
        if not payload.get("available"):
            payload = _mock_payload()
            payload["fallback"] = True
    else:
        api_key = str(os.getenv("UMAMI_API_KEY", "") or "").strip()
        if not api_key:
            payload = {
                "available": False,
                "reason": "not_configured",
                "privacy_note": (
                    "Anonymous aggregate counts from Umami Analytics are shown here when "
                    "configured. MERIT-ML does not add separate visitor tracking for this display."
                ),
            }
        else:
            payload = _fetch_umami_stats()

    _CACHE["fetched_at"] = now
    _CACHE["payload"] = payload
    return dict(payload)
