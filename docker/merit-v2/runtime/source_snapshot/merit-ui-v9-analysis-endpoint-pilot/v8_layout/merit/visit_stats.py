from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_UMAMI_WEBSITE_ID = "e9fa298c-3199-4358-a0ec-8fa401f3eb10"
_DEFAULT_UMAMI_API_BASE = "https://api.umami.is/v1"
_DEFAULT_CLOUDFLARE_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
_DEFAULT_CLOUDFLARE_REST_BASE = "https://api.cloudflare.com/client/v4"
# MERIT-ML Cloudflare account + Web Analytics dashboard from:
# https://dash.cloudflare.com/147880730eec94d25798cfb573c28bf1/dashboards/507733aa-714f-4278-9352-f5b9a996d0a4
_DEFAULT_CLOUDFLARE_ACCOUNT_ID = "147880730eec94d25798cfb573c28bf1"
_DEFAULT_CLOUDFLARE_WEB_ANALYTICS_SITE_ID = "507733aa-714f-4278-9352-f5b9a996d0a4"
# Analytics went live with the public MERIT-ML site rollout.
_DEFAULT_STATS_START_UTC = datetime(2025, 6, 20, tzinfo=timezone.utc)
_CACHE: dict[str, Any] = {"fetched_at": 0.0, "payload": None}
_RUM_SITE_CACHE: dict[str, Any] = {"fetched_at": 0.0, "config": None}


def _stats_mode() -> str:
    raw = str(os.getenv("MERIT_VISIT_STATS_MODE", "auto") or "auto").strip().lower()
    if raw in {"mock", "demo", "off", "disabled", "live", "auto"}:
        return raw
    return "auto"


def _stats_provider() -> str:
    configured = str(os.getenv("MERIT_VISIT_STATS_PROVIDER", "auto") or "auto").strip().lower()
    if configured in {"cloudflare", "cf", "umami", "auto"}:
        return configured
    return "auto"


def _cloudflare_configured() -> bool:
    token = str(os.getenv("CLOUDFLARE_API_TOKEN", "") or "").strip()
    account_id = _cloudflare_account_id()
    site_tag = str(os.getenv("CLOUDFLARE_WEB_ANALYTICS_SITE_TAG", "") or "").strip()
    site_id = _cloudflare_web_analytics_site_id()
    return bool(token and account_id and (site_tag or site_id))


def _cloudflare_account_id() -> str:
    configured = str(os.getenv("CLOUDFLARE_ACCOUNT_ID", "") or "").strip()
    if configured:
        return configured
    parsed = _parse_cloudflare_dashboard_url(
        str(os.getenv("CLOUDFLARE_WEB_ANALYTICS_DASHBOARD_URL", "") or "").strip()
    )
    if parsed:
        return parsed[0]
    return _DEFAULT_CLOUDFLARE_ACCOUNT_ID


def _cloudflare_web_analytics_site_id() -> str:
    configured = str(
        os.getenv("CLOUDFLARE_WEB_ANALYTICS_SITE_ID", "")
        or os.getenv("CLOUDFLARE_WEB_ANALYTICS_DASHBOARD_ID", "")
        or ""
    ).strip()
    if configured:
        return configured
    parsed = _parse_cloudflare_dashboard_url(
        str(os.getenv("CLOUDFLARE_WEB_ANALYTICS_DASHBOARD_URL", "") or "").strip()
    )
    if parsed:
        return parsed[1]
    return _DEFAULT_CLOUDFLARE_WEB_ANALYTICS_SITE_ID


def _parse_cloudflare_dashboard_url(url: str) -> tuple[str, str] | None:
    if not url:
        return None
    match = re.search(
        r"https?://dash\.cloudflare\.com/([0-9a-f]+)/dashboards/([0-9a-f-]+)",
        url,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1), match.group(2)


def _cloudflare_api_token() -> str:
    return str(os.getenv("CLOUDFLARE_API_TOKEN", "") or "").strip()


def _cloudflare_rest_request(path: str) -> dict[str, Any]:
    token = _cloudflare_api_token()
    if not token:
        return {"ok": False, "reason": "missing_api_token"}
    url = f"{_DEFAULT_CLOUDFLARE_REST_BASE.rstrip('/')}/{path.lstrip('/')}"
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "MERIT-ML/1.0 (+https://www.merit-ml.in)",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:320]
        except Exception:
            detail = ""
        return {"ok": False, "reason": f"cloudflare_http_{exc.code}", "detail": detail}
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "cloudflare_fetch_failed", "detail": str(exc)}
    if not payload.get("success"):
        return {
            "ok": False,
            "reason": "cloudflare_api_error",
            "detail": json.dumps(payload.get("errors") or payload, ensure_ascii=False)[:320],
        }
    return {"ok": True, "result": payload.get("result")}


def _extract_beacon_token(snippet: str) -> str:
    if not snippet:
        return ""
    match = re.search(r"data-cf-beacon=['\"](\{.*?\})['\"]", snippet, flags=re.DOTALL)
    if not match:
        match = re.search(r'"token"\s*:\s*"([^"]+)"', snippet)
        return match.group(1) if match else ""
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return ""
    return str(payload.get("token") or "").strip()


def _rum_site_cache_ttl_seconds() -> int:
    try:
        return max(300, int(os.getenv("CLOUDFLARE_RUM_SITE_CACHE_SECONDS", "21600")))
    except ValueError:
        return 21600


def resolve_cloudflare_rum_site(*, force_refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    if (
        not force_refresh
        and _RUM_SITE_CACHE.get("config") is not None
        and (now - float(_RUM_SITE_CACHE.get("fetched_at") or 0.0)) < _rum_site_cache_ttl_seconds()
    ):
        return dict(_RUM_SITE_CACHE["config"])

    configured_site_tag = str(os.getenv("CLOUDFLARE_WEB_ANALYTICS_SITE_TAG", "") or "").strip()
    configured_beacon = str(os.getenv("CLOUDFLARE_WEB_ANALYTICS_BEACON_TOKEN", "") or "").strip()
    if configured_site_tag and configured_beacon:
        payload = {
            "available": True,
            "account_id": _cloudflare_account_id(),
            "site_id": _cloudflare_web_analytics_site_id(),
            "site_tag": configured_site_tag,
            "beacon_token": configured_beacon,
            "source": "env",
        }
        _RUM_SITE_CACHE["fetched_at"] = now
        _RUM_SITE_CACHE["config"] = payload
        return dict(payload)

    account_id = _cloudflare_account_id()
    site_id = _cloudflare_web_analytics_site_id()
    if not account_id or not site_id:
        return {"available": False, "reason": "missing_site_id"}

    api_result = _cloudflare_rest_request(f"accounts/{account_id}/rum/site_info/{site_id}")
    if not api_result.get("ok"):
        site_tag = configured_site_tag or site_id
        if site_tag and _cloudflare_api_token():
            payload = {
                "available": True,
                "account_id": account_id,
                "site_id": site_id,
                "site_tag": site_tag,
                "beacon_token": configured_beacon,
                "host": "",
                "source": "dashboard_fallback",
            }
            _RUM_SITE_CACHE["fetched_at"] = now
            _RUM_SITE_CACHE["config"] = payload
            return dict(payload)
        return {"available": False, **api_result}

    result = api_result.get("result") or {}
    site_tag = str(result.get("site_tag") or configured_site_tag or "").strip()
    beacon_token = _extract_beacon_token(str(result.get("snippet") or "")) or configured_beacon
    host = ""
    rules = result.get("rules") or []
    if rules and isinstance(rules[0], dict):
        host = str(rules[0].get("host") or "").strip()

    if not site_tag:
        return {"available": False, "reason": "missing_site_tag"}

    payload = {
        "available": True,
        "account_id": account_id,
        "site_id": site_id,
        "site_tag": site_tag,
        "beacon_token": beacon_token,
        "host": host,
        "source": "cloudflare_api",
    }
    _RUM_SITE_CACHE["fetched_at"] = now
    _RUM_SITE_CACHE["config"] = payload
    return dict(payload)


def get_cloudflare_beacon_token() -> str:
    configured = str(os.getenv("CLOUDFLARE_WEB_ANALYTICS_BEACON_TOKEN", "") or "").strip()
    if configured:
        return configured
    resolved = resolve_cloudflare_rum_site()
    if resolved.get("available"):
        return str(resolved.get("beacon_token") or "").strip()
    return ""


def _resolved_cloudflare_site_tag() -> str:
    configured = str(os.getenv("CLOUDFLARE_WEB_ANALYTICS_SITE_TAG", "") or "").strip()
    if configured:
        return configured
    resolved = resolve_cloudflare_rum_site()
    if resolved.get("available"):
        return str(resolved.get("site_tag") or "").strip()
    return ""


def _umami_configured() -> bool:
    return bool(str(os.getenv("UMAMI_API_KEY", "") or "").strip())


def _resolved_provider() -> str:
    provider = _stats_provider()
    if provider == "cloudflare":
        return "cloudflare"
    if provider == "umami":
        return "umami"
    if _cloudflare_configured():
        return "cloudflare"
    if _umami_configured():
        return "umami"
    return "none"


def _cache_ttl_seconds() -> int:
    try:
        return max(60, int(os.getenv("MERIT_VISIT_STATS_CACHE_SECONDS", "3600")))
    except ValueError:
        return 3600


def _stats_start_utc() -> datetime:
    configured = str(os.getenv("MERIT_VISIT_STATS_START_MS", "") or "").strip()
    if configured.isdigit():
        return datetime.fromtimestamp(int(configured) / 1000, tz=timezone.utc)
    configured_date = str(os.getenv("MERIT_VISIT_STATS_START_DATE", "") or "").strip()
    if configured_date:
        try:
            return datetime.strptime(configured_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return _DEFAULT_STATS_START_UTC


def _stats_start_ms() -> int:
    return int(_stats_start_utc().timestamp() * 1000)


def _period_label(start: datetime) -> str:
    try:
        return f"since {start.strftime('%B %Y')}"
    except (OverflowError, OSError, ValueError):
        return "all time"


def _mock_payload() -> dict[str, Any]:
    visitors = int(os.getenv("MERIT_VISIT_STATS_MOCK_VISITORS", "1284") or "1284")
    pageviews = int(os.getenv("MERIT_VISIT_STATS_MOCK_PAGEVIEWS", "3567") or "3567")
    start = _stats_start_utc()
    return {
        "available": True,
        "mock": True,
        "visitors": visitors,
        "pageviews": pageviews,
        "visitor_label": "Visits",
        "pageview_label": "Page views",
        "period_label": _period_label(start),
        "source": "mock",
        "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "privacy_note": (
            "Demo numbers for local UI preview. Production uses anonymous aggregate totals "
            "from Cloudflare Web Analytics; no personal data is collected or displayed."
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


def _graphql_request(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    token = _cloudflare_api_token()
    if not token:
        return {"available": False, "reason": "missing_api_token"}

    endpoint = str(
        os.getenv("CLOUDFLARE_GRAPHQL_URL", _DEFAULT_CLOUDFLARE_GRAPHQL_URL) or _DEFAULT_CLOUDFLARE_GRAPHQL_URL
    ).strip()
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "MERIT-ML/1.0 (+https://www.merit-ml.in)",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:320]
        except Exception:
            detail = ""
        return {"available": False, "reason": f"cloudflare_http_{exc.code}", "detail": detail}
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": "cloudflare_fetch_failed", "detail": str(exc)}

    if payload.get("errors"):
        return {
            "available": False,
            "reason": "cloudflare_graphql_error",
            "detail": json.dumps(payload.get("errors"), ensure_ascii=False)[:320],
        }
    return {"available": True, "data": payload.get("data") or {}}


def _fetch_cloudflare_stats() -> dict[str, Any]:
    account_id = _cloudflare_account_id()
    site_tag = _resolved_cloudflare_site_tag()
    if not account_id or not site_tag:
        return {"available": False, "reason": "missing_cloudflare_config"}

    start = _stats_start_utc()
    end = datetime.now(timezone.utc)
    # Cloudflare Web Analytics GraphQL retains roughly the last 30 days of RUM data.
    rum_floor = end - timedelta(days=30)
    if start < rum_floor:
        start = rum_floor

    query = """
query MeritVisitStats($accountTag: string!, $filter: AccountRumPageloadEventsAdaptiveGroupsFilter_InputObject!) {
  viewer {
    accounts(filter: { accountTag: $accountTag }) {
      total: rumPageloadEventsAdaptiveGroups(filter: $filter, limit: 1) {
        count
        sum {
          visits
        }
      }
    }
  }
}
""".strip()
    variables = {
        "accountTag": account_id,
        "filter": {
            "AND": [
                {
                    "datetime_geq": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "datetime_leq": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                {"siteTag": site_tag},
            ]
        },
    }
    result = _graphql_request(query, variables)
    if not result.get("available"):
        return result

    accounts = (((result.get("data") or {}).get("viewer") or {}).get("accounts") or [])
    if not accounts:
        return {"available": False, "reason": "cloudflare_empty_response"}

    groups = accounts[0].get("total") or []
    if not groups:
        pageviews = 0
        visits = 0
    else:
        group = groups[0] or {}
        pageviews = _metric_value(group, "count")
        visits = _metric_value((group.get("sum") or {}), "visits")

    period = _period_label(_stats_start_utc())
    if start > _stats_start_utc():
        period = f"last 30 days (Cloudflare retention limit)"

    return {
        "available": True,
        "mock": False,
        "visitors": visits,
        "pageviews": pageviews,
        "visitor_label": "Visits",
        "pageview_label": "Page views",
        "period_label": period,
        "source": "cloudflare",
        "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "privacy_note": (
            "Anonymous aggregate totals from Cloudflare Web Analytics. MERIT-ML does not store "
            "visitor identities for this display. Visits are Cloudflare's cookieless session "
            "estimate; page views count real-user page loads recorded by the analytics beacon."
        ),
    }


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
        "visitor_label": "Unique visitors",
        "pageview_label": "Page views",
        "period_label": _period_label(_stats_start_utc()),
        "source": "umami",
        "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "privacy_note": (
            "Anonymous aggregate counts from Umami Analytics. MERIT-ML does not store IP "
            "addresses, accounts, or personal identifiers for this display. The site only loads "
            "Umami after you accept the analytics notice; declined visits are not counted."
        ),
    }


def _not_configured_payload(provider: str) -> dict[str, Any]:
    if provider == "cloudflare":
        note = (
            "Anonymous aggregate totals from Cloudflare Web Analytics are shown here when "
            "configured. Set CLOUDFLARE_API_TOKEN (Account Analytics Read). Account and "
            "dashboard IDs are already wired for MERIT-ML; site tag and beacon resolve "
            "automatically from the dashboard URL."
        )
    else:
        note = (
            "Anonymous aggregate counts are shown here when analytics is configured. "
            "MERIT-ML does not add separate visitor tracking for this display."
        )
    return {"available": False, "reason": "not_configured", "privacy_note": note}


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

    provider = _resolved_provider()
    if mode in {"mock", "demo"}:
        payload = _mock_payload()
    elif mode == "live":
        if provider == "cloudflare":
            payload = _fetch_cloudflare_stats()
        elif provider == "umami":
            payload = _fetch_umami_stats()
        else:
            payload = _not_configured_payload("cloudflare")
        if not payload.get("available"):
            payload = _mock_payload()
            payload["fallback"] = True
    elif provider == "cloudflare":
        payload = _fetch_cloudflare_stats()
    elif provider == "umami":
        payload = _fetch_umami_stats()
    else:
        payload = _not_configured_payload("cloudflare")

    _CACHE["fetched_at"] = now
    _CACHE["payload"] = payload
    return dict(payload)
