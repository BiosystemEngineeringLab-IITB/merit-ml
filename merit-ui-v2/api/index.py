from __future__ import annotations

import os
import json

from flask import Flask, Response, request

from merit.ui import (
    _bulk_chunk_payload,
    _bulk_clean_session,
    _bulk_ml_ready_data_zip_payload,
    _bulk_runner_page,
    _embargoed_study_message,
    _load_cached_workflow_state,
    _load_precomputed_state,
    _ml_ready_data_zip_payload,
    _page,
    _requested_fetch_mode,
    _logo_asset_bytes,
    _study_browser_data_payload,
)


def _cache_only() -> bool:
    value = str(os.getenv("MERIT_CACHE_ONLY", "1")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _default_precomputed_root() -> str:
    configured = (
        os.getenv("MERIT_UI_PRECOMPUTED_ROOT")
        or os.getenv("MERIT_PRECOMPUTED_ROOT")
        or os.getenv("MERIT_CACHE_BASE_URL")
    )
    if configured:
        return configured
    candidates = [
        os.path.join(os.getcwd(), "merit-cache-workbench-full-v7"),
        os.path.join(os.path.dirname(os.getcwd()), "merit-cache-workbench-full-v7"),
        "/home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return "merit-cache-workbench-full-v7"


app = Flask(__name__)


@app.get("/")
def home() -> Response:
    study_id = (request.args.get("study_id") or "").strip()
    requested_profile = (request.args.get("profile") or "full").strip() or "full"
    defaults = {
        "source": "workbench",
        "profile": requested_profile,
        "precomputed_root": _default_precomputed_root(),
        "study_id": study_id,
    }
    if study_id:
        try:
            cached_state = _load_precomputed_state(
                study_id=study_id,
                precomputed_root=_default_precomputed_root(),
                requested_profile=requested_profile,
            )
            if cached_state is not None:
                return Response(_page(state=cached_state, defaults=defaults), mimetype="text/html")
            embargo_message = _embargoed_study_message(study_id)
            if embargo_message:
                return Response(_page(error=embargo_message, defaults=defaults), status=403, mimetype="text/html")
            return Response(
                _page(error=f"The study {study_id} is not available in the current version of MERIT.", defaults=defaults),
                status=404,
                mimetype="text/html",
            )
        except Exception as exc:
            return Response(_page(error=str(exc), defaults=defaults), status=400, mimetype="text/html")
    return Response(_page(defaults=defaults), mimetype="text/html")


@app.get("/study-browser-data")
def study_browser_data() -> Response:
    query = request.args.to_dict(flat=True)
    precomputed_root = query.get("precomputed_root") or _default_precomputed_root()
    payload = _study_browser_data_payload(precomputed_root, query)
    return Response(
        json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "private, max-age=60"},
    )


@app.get("/assets/logo.png")
def logo_asset() -> Response:
    asset = _logo_asset_bytes()
    if asset is None:
        return Response("Logo not found.", status=404, mimetype="text/plain")
    payload, mime = asset
    return Response(payload, mimetype=mime, headers={"Cache-Control": "public, max-age=86400"})


@app.post("/workflow/load-state")
def load_state() -> Response:
    form = request.form.to_dict(flat=True)
    defaults = dict(form)
    defaults["source"] = "workbench"
    state_path = (form.get("state_path") or "").strip()
    if not state_path:
        return Response(_page(error="State JSON path is required.", defaults=defaults), status=400, mimetype="text/html")
    try:
        state = _load_cached_workflow_state(state_path)
        embargo_message = _embargoed_study_message(state.get("study_id") if isinstance(state, dict) else "")
        if embargo_message:
            return Response(_page(error=embargo_message, defaults=defaults), status=403, mimetype="text/html")
    except Exception as exc:
        return Response(_page(error=str(exc), defaults=defaults), status=400, mimetype="text/html")
    return Response(_page(state=state, defaults=defaults), mimetype="text/html")


@app.post("/workflow/run")
def workflow_run() -> Response:
    form = request.form.to_dict(flat=True)
    defaults = dict(form)
    defaults["source"] = "workbench"

    study_id = (form.get("study_id") or "").strip()
    if not study_id:
        return Response(_page(error="Study ID is required.", defaults=defaults), status=400, mimetype="text/html")

    requested_profile = form.get("profile", "full")
    precomputed_root = (
        form.get("precomputed_root")
        or form.get("output_root")
        or _default_precomputed_root()
    )
    try:
        embargo_message = _embargoed_study_message(study_id)
        if embargo_message:
            return Response(_page(error=embargo_message, defaults=defaults), status=403, mimetype="text/html")
        cached_state = _load_precomputed_state(
            study_id=study_id,
            precomputed_root=precomputed_root,
            requested_profile=requested_profile,
        )
        if cached_state is not None:
            return Response(_page(state=cached_state, defaults=defaults), mimetype="text/html")

        if _cache_only():
            msg = (
                f"No MERIT assessment is available for {study_id} in the current release."
            )
            return Response(_page(error=msg, defaults=defaults), status=404, mimetype="text/html")

        from merit.workflow import run_guided_workflow

        state = run_guided_workflow(
            source="workbench",
            study_id=study_id,
            profile=requested_profile,
            fetch_mode=_requested_fetch_mode("workbench", allow_remote_fallback=False),
            root=form.get("root") or None,
            download_root=form.get("download_root") or None,
            output_root=form.get("output_root") or None,
        )
        return Response(_page(state=state, defaults=defaults), mimetype="text/html")
    except Exception as exc:
        return Response(_page(error=str(exc), defaults=defaults), status=400, mimetype="text/html")


@app.post("/bulk/run")
def bulk_run() -> Response:
    form = request.form.to_dict(flat=True)
    defaults = dict(form)
    defaults["source"] = "workbench"
    precomputed_root = (
        form.get("precomputed_root")
        or form.get("output_root")
        or _default_precomputed_root()
    )
    try:
        session = _bulk_clean_session(form.get("bulk_session", ""))
        return Response(
            _bulk_runner_page(session, precomputed_root),
            mimetype="text/html",
        )
    except Exception as exc:
        return Response(_page(error=str(exc), defaults=defaults), status=400, mimetype="text/html")


@app.post("/bulk/chunk")
def bulk_chunk() -> Response:
    form = request.form.to_dict(flat=True)
    precomputed_root = (
        form.get("precomputed_root")
        or form.get("output_root")
        or _default_precomputed_root()
    )
    try:
        payload = _bulk_chunk_payload(form.get("bulk_session", ""), precomputed_root)
        payload["chunk_start"] = int(form.get("chunk_start", 0) or 0)
        payload["chunk_size"] = int(form.get("chunk_size", payload.get("n_used", 0)) or 0)
        return Response(
            json.dumps(payload, ensure_ascii=False),
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:
        return Response(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
            status=400,
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )


@app.post("/download/ml-ready-data")
def download_ml_ready_data() -> Response:
    form = request.form.to_dict(flat=True)
    try:
        payload, filename = _ml_ready_data_zip_payload(
            form.get("study_id", ""),
            form.get("matrix_overrides", "{}"),
            form.get("analysis_ids", ""),
        )
        return Response(
            payload,
            mimetype="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    except Exception as exc:
        defaults = dict(form)
        defaults["source"] = "workbench"
        return Response(_page(error=str(exc), defaults=defaults), status=400, mimetype="text/html")


@app.post("/bulk/download-data")
def bulk_download_data() -> Response:
    form = request.form.to_dict(flat=True)
    try:
        payload, filename = _bulk_ml_ready_data_zip_payload(form.get("bulk_session", ""))
        return Response(
            payload,
            mimetype="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    except Exception as exc:
        defaults = dict(form)
        defaults["source"] = "workbench"
        return Response(_page(error=str(exc), defaults=defaults), status=400, mimetype="text/html")


@app.get("/healthz")
def healthz() -> Response:
    return Response("ok", mimetype="text/plain")
