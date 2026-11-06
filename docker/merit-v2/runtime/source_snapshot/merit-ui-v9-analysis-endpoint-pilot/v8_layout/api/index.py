from __future__ import annotations

import os
import json
import csv
import io
import re
from pathlib import Path

from flask import Flask, Response, request

from merit.visit_stats import get_public_visit_stats
from merit.acquisition_context import CONTEXT_KEY
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
        os.path.join(os.getcwd(), "merit-cache-workbench-full-v8"),
        os.path.join(os.path.dirname(os.getcwd()), "merit-cache-workbench-full-v8"),
        "/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit-cache-workbench-full-v8",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return "merit-cache-workbench-full-v8"


app = Flask(__name__)


@app.after_request
def _persist_theme(response: Response) -> Response:
    if response.mimetype != "text/html":
        return response
    page = response.get_data(as_text=True)
    if "id=\"theme-toggle\"" in page or "id='theme-toggle'" in page or "id=\"merit-theme-toggle\"" in page:
        return response
    css = """<style id="merit-theme-css">
html[data-theme="dark"]{--ink:#e8eaed;--muted:#b4c0c3;--bg:#111518;--panel:#1b2226}
html[data-theme="dark"] [style*="background:rgba(255,255,255"],html[data-theme="dark"] [style*="background:#fff"],html[data-theme="dark"] [style*="background:white"]{background:#1b2226!important;color:#e8eaed!important}
html[data-theme="dark"] [style*="color:#132327"],html[data-theme="dark"] [style*="color:#51656a"],html[data-theme="dark"] h2,html[data-theme="dark"] h3,html[data-theme="dark"] h4{color:#dce6e9!important}
html[data-theme="dark"] body{background:#111518!important;color:#e8eaed!important}
html[data-theme="dark"] .panel,html[data-theme="dark"] .card,html[data-theme="dark"] .about-card,html[data-theme="dark"] .v2-tune,html[data-theme="dark"] .tab-panel,html[data-theme="dark"] .tablewrap,html[data-theme="dark"] details{background:#1b2226!important;color:#e8eaed!important;border-color:#394247!important}
html[data-theme="dark"] input,html[data-theme="dark"] select,html[data-theme="dark"] textarea{background:#20282d!important;color:#e8eaed!important;border-color:#465158!important}
html[data-theme="dark"] p,html[data-theme="dark"] li,html[data-theme="dark"] td,html[data-theme="dark"] th,html[data-theme="dark"] label,html[data-theme="dark"] summary{color:#c5ced1}
#merit-theme-toggle{position:fixed;right:16px;top:14px;z-index:99999;width:38px;height:38px;border:1px solid #c7d0d2;border-radius:50%;background:#fff;color:#263238;font:18px Arial;cursor:pointer;box-shadow:0 2px 8px #0002}
html[data-theme="dark"] #merit-theme-toggle{background:#20282d;color:#f3f5f6;border-color:#536168}
</style>"""
    control = """<button id="merit-theme-toggle" type="button" aria-label="Switch to dark theme" title="Switch color theme">◐</button>
<script>(function(){var r=document.documentElement,k='merit-ml-theme';try{r.dataset.theme=localStorage.getItem(k)||'light'}catch(e){r.dataset.theme='light'}var b=document.getElementById('merit-theme-toggle');function paint(){var d=r.dataset.theme==='dark';b.textContent=d?'☼':'◐';b.setAttribute('aria-label',d?'Switch to light theme':'Switch to dark theme')}paint();b.addEventListener('click',function(){r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';try{localStorage.setItem(k,r.dataset.theme)}catch(e){}paint()})})();</script>"""
    page = page.replace("</head>", css + "</head>", 1).replace("</body>", control + "</body>", 1)
    response.set_data(page)
    return response


@app.get("/download/acquisition-evidence")
def download_acquisition_evidence() -> Response:
    """Download reviewed evidence for the displayed study, without scoring it."""
    study_id = (request.args.get("study_id") or "").strip().upper()
    if not re.fullmatch(r"ST\d{6}", study_id):
        return Response("A valid study ID is required.", status=400, mimetype="text/plain")
    embargo_message = _embargoed_study_message(study_id)
    if embargo_message:
        return Response(embargo_message, status=403, mimetype="text/plain")
    state = _load_precomputed_state(
        study_id=study_id, precomputed_root=_default_precomputed_root(), requested_profile="full",
    )
    context = state.get(CONTEXT_KEY, {}) if state else {}
    records = context.get("records") or []
    if not records:
        return Response("No acquisition evidence is available for this study.", status=404, mimetype="text/plain")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(records[0]), delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return Response(
        output.getvalue(), mimetype="text/tab-separated-values",
        headers={"Content-Disposition": f'attachment; filename="{study_id}_acquisition_evidence.tsv"',
                 "Cache-Control": "no-store"},
    )


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
    if not study_id:
        landing = next((root / "online-tool/assets/landing.html" for root in Path(__file__).resolve().parents if (root / "online-tool/assets/landing.html").is_file()), Path("/missing-landing.html"))
        if landing.exists():
            page = landing.read_text(encoding="utf-8").replace("__MERIT_MODE__", "cache").replace("__MERIT_APP_SCRIPT__", "")
            return Response(page, mimetype="text/html", headers={"Cache-Control": "no-store"})
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


@app.get("/workspace")
def workspace() -> Response:
    return Response(status=302,headers={"Location":"/"})


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
@app.get("/assets/merit-logo.svg")
@app.get("/assets/merit-logo-dark.svg")
def logo_asset() -> Response:
    asset = _logo_asset_bytes(request.path.endswith("merit-logo-dark.svg"))
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


@app.post("/api/bulk/validate")
def bulk_validate():
    import sys
    home=next(root / "online-tool" for root in Path(__file__).resolve().parents if (root / "online-tool/bulk_entry.py").exists())
    if str(home) not in sys.path:sys.path.append(str(home))
    from bulk_entry import validate
    index=json.loads((Path(_default_precomputed_root()) / "index.json").read_text()).get("studies",{})
    def classify(sid):
        embargo=_embargoed_study_message(sid)
        if embargo:return dict(status="embargoed",message=embargo)
        return dict(status="available" if sid in index else "unavailable",message="Available in the frozen reference cohort" if sid in index else "Not available in the frozen reference cohort")
    try:return Response(json.dumps(validate((request.get_json(silent=True) or {}).get("text"),classify)),mimetype="application/json")
    except ValueError as exc:return Response(json.dumps(dict(ok=False,error=str(exc))),status=400,mimetype="application/json")


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
            __import__("merit.bulk_profile_ui",fromlist=["page"]).page({**session,"input_review":json.loads(form.get("bulk_session","{} ")).get("input_review",[])}, Path(__file__).resolve().parents[1]/"merit/ui.py"),
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
        from merit import pilot_analysis_endpoint as pilot, ui
        from merit.bulk_profile_ui import rows
        session=_bulk_clean_session(form.get("bulk_session", ""))
        summaries=[];metrics=[]
        for item in session['studies']:
            sid=item['study_id']
            if _embargoed_study_message(sid):raise ValueError(_embargoed_study_message(sid))
            value=pilot.public(pilot.export_overview_block(sid,ui._coerce_v2_scoring_params(item.get('scoring_params') or {}),item.get('matrix_overrides') or {}))
            block=rows(value,sid);summaries.extend(block['summary_rows']);metrics.extend(block['metric_rows'])
        payload=dict(ok=True,summary_rows=summaries,metric_rows=metrics,n_used=len(session['studies']),errors=[])
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


@app.post("/download/v9-matrix")
def download_v9_matrix() -> Response:
    """One matrix (analysis x endpoint) from the frozen deposit, with the sample edits and parameters in force."""
    from merit import v9_matrix_export
    from merit.ui import _coerce_v2_matrix_overrides, _coerce_v2_scoring_params
    form = request.form.to_dict(flat=True)
    try:
        payload, filename = v9_matrix_export.matrix_zip(
            form.get("study_id", ""), form.get("analysis_id", ""), form.get("endpoint", ""),
            _coerce_v2_scoring_params(form), _coerce_v2_matrix_overrides(form),
        )
        return Response(payload, mimetype="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})
    except Exception as exc:
        return Response(json.dumps({"ok": False, "error": str(exc)}), status=400, mimetype="application/json")


@app.post("/download/ml-ready-matrix")
def download_ml_ready_matrix() -> Response:
    """The ML-ready matrix of one analysis x endpoint (sample ID, subject ID, class in force, factor string, deposited values), built
    when the button is clicked from the deposit files the page assessed, with the class choice and sample edits in force."""
    from merit import v9_matrix_export
    from merit.ui import _coerce_v2_matrix_overrides, _coerce_v2_scoring_params
    form = request.form.to_dict(flat=True)
    params = _coerce_v2_scoring_params(form); params["label_classes"] = str(form.get("label_classes", "") or "")
    try:
        payload, filename = v9_matrix_export.ml_ready_zip(form.get("study_id", ""), form.get("analysis_id", ""), form.get("endpoint", ""),
                                                         params, _coerce_v2_matrix_overrides(form))
        return Response(payload, mimetype="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})
    except Exception as exc:
        return Response(json.dumps({"ok": False, "error": str(exc)}), status=400, mimetype="application/json")


@app.post("/download/feature-list")
def download_feature_list() -> Response:
    """Full feature list of one study for Low-information features or Redundant features, every analysis x endpoint, built when the
    link is clicked from the stored cache with the parameters and sample edits in force. Nothing is written to disk."""
    from merit import pilot_analysis_endpoint as pilot
    from merit.ui import _coerce_v2_matrix_overrides, _coerce_v2_scoring_params
    form = request.form.to_dict(flat=True)
    study_id = (form.get("study_id") or "").strip().upper(); metric = (form.get("metric") or "").strip()
    if not re.fullmatch(r"ST\d{6}", study_id) or metric not in ("low_information", "redundancy", "outliers", "qc_reproducibility", "controls", "annotation"):
        return Response("A valid study ID and metric are required.", status=400, mimetype="text/plain")
    try:
        payload = pilot.public(pilot.feature_list_payload(study_id, metric, _coerce_v2_scoring_params(form), _coerce_v2_matrix_overrides(form)))
    except Exception as exc:
        return Response(json.dumps({"ok": False, "error": str(exc)}), status=400, mimetype="application/json")
    name = {"low_information": "low_information_features", "redundancy": "redundant_features", "outliers": "outlier_samples", "qc_reproducibility": "qc_reproducibility", "controls": "analytical_controls", "annotation": "annotation_features"}[metric]
    return Response(json.dumps(payload, ensure_ascii=False, indent=1), mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{study_id}_{name}.json"', "Cache-Control": "no-store"})


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


@app.get("/api/visit-stats")
def visit_stats() -> Response:
    payload = get_public_visit_stats()
    return Response(
        json.dumps(payload),
        mimetype="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )
