from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Iterable

from merit.models import AssessmentReport, MetricResult


def _iter_sections(report: AssessmentReport) -> list[tuple[str, Iterable[MetricResult]]]:
    return [
        ("Schema Validation", report.schema_validation),
        ("Metadata Readiness", report.metadata_readiness),
        ("Analytical Readiness", report.analytical_readiness),
        ("Annotation Readiness", report.annotation_readiness),
        ("Cohort Bias", report.cohort_bias),
        ("ML Feasibility", report.ml_readiness),
        ("Class Separability", report.class_separability),
        ("Cross-Study Harmonization", report.cross_study_harmonization),
    ]


def render_markdown(report: AssessmentReport) -> str:
    lines = [
        f"# MERIT Report: {report.source['study_id']}",
        "",
        f"- Repository: `{report.source['repository']}`",
        f"- Study: `{report.source['title']}`",
        f"- Content hash: `{report.content_hash}`",
        "",
        "## Ingestion Summary",
        "",
    ]
    for key, value in report.ingestion_summary.items():
        lines.append(f"- {key}: `{value}`")
    for title, metrics in _iter_sections(report):
        lines.append("")
        lines.append(f"## {title}")
        lines.append("")
        for metric in metrics:
            lines.append(f"### {metric.name}")
            lines.append("")
            lines.append(f"- Family: `{metric.family}`")
            lines.append(f"- Score: `{metric.score:.3f}`")
            lines.append(f"- Status: `{metric.status}`")
            lines.append(f"- Summary: {metric.summary}")
            if metric.recommendations:
                lines.append(f"- Recommendation: {metric.recommendations[0]}")
            lines.append("")
    if report.remediations_applied:
        lines.extend(["## Remediations", ""])
        for item in report.remediations_applied:
            lines.append(f"- `{item['action']}`: `{item}`")
    return "\n".join(lines).strip() + "\n"


def render_html(report: AssessmentReport) -> str:
    body = [f"<h1>MERIT Report: {escape(report.source['study_id'])}</h1>"]
    body.append("<ul>")
    body.append(f"<li>Repository: <code>{escape(report.source['repository'])}</code></li>")
    body.append(f"<li>Study: <code>{escape(report.source['title'])}</code></li>")
    body.append(f"<li>Content hash: <code>{escape(report.content_hash)}</code></li>")
    body.append("</ul>")
    for title, metrics in _iter_sections(report):
        body.append(f"<h2>{escape(title)}</h2>")
        body.append("<table border='1' cellpadding='6' cellspacing='0'>")
        body.append("<tr><th>Name</th><th>Score</th><th>Status</th><th>Summary</th></tr>")
        for metric in metrics:
            body.append(
                "<tr>"
                f"<td>{escape(metric.name)}</td>"
                f"<td>{metric.score:.3f}</td>"
                f"<td>{escape(metric.status)}</td>"
                f"<td>{escape(metric.summary)}</td>"
                "</tr>"
            )
        body.append("</table>")
    return "<!doctype html><html><body>" + "\n".join(body) + "</body></html>\n"


def write_rendered_report(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
