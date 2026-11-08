"""The same presentation-only tour is embedded in reports and landing pages."""
from pathlib import Path


def tour_html() -> str:
    source = Path(__file__).resolve().parents[1] / "static" / "merit-tour.js"
    return '<script id="merit-guided-tour">' + source.read_text(encoding="utf-8") + '</script>'
