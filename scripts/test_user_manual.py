"""Reject internal deployment wording and stale numerical settings in public manuals."""
from pathlib import Path
from html.parser import HTMLParser
import ast
import json
import re

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / 'docker/merit-v2/runtime/source_snapshot/merit-ui-v9-analysis-endpoint-pilot/v8_layout/static/manual'
MODULES = MANUAL.parents[1] / 'merit'

class ReaderText(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'svg'}: self.skip += 1
    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'svg'}: self.skip -= 1
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)

FORBIDDEN = (
    r'\bv[789]\b', r'\bv\d+-live-\d+', r'\bcache\b', r'\bfrozen\b',
    r'/home/', r'file://', r'8782|8791', r'202611\d+', r'v8_layout',
    r'\bsource_files\b', r'\bsource_sha256\b', r'\bcodebase\b',
    r'\bregistered\b', r'\bbenchmark\b', r'\bretired\b',
    r'\bserver\b', r'\bmanuscript\b',
)

LABELS = {
    'cv_folds': 'Cross-validation folds k',
    'structural_sample_support_n': 'Structural: sample-support target N',
    'low_information_freq_ratio': 'Low-information: frequency ratio R',
    'low_information_unique_pct': 'Low-information: distinct-value percentage U',
    'low_information_cv_pct': 'Low-variation caution: CV (%)',
    'redundancy_cutoff': 'Redundancy: absolute correlation c',
    'qc_rsd_cutoff': 'QC reproducibility: RSD (%)',
    'outlier_iqr_k': 'Outliers: fence multiplier a',
    'readiness_extreme_pct': 'Extreme-alert percentile',
    'readiness_low_pct': 'Low-alert percentile',
    'sep_k': 'Separability: neighbours K',
    'sep_theta': 'Separability: overlap threshold θ',
    'sep_fdr': 'Feature associations: FDR q',
}

def main():
    for name in ('MERIT-ML_User_Manual.md', 'index.html', 'coverage_manifest.json'):
        text = (MANUAL / name).read_text()
        if name.endswith('.html'):
            parser = ReaderText(); parser.feed(text); text = ' '.join(parser.parts)
        for pattern in FORBIDDEN:
            assert not re.search(pattern, text, re.I), (name, pattern)
    index = json.loads((MANUAL / 'coverage_manifest.json').read_text())
    assert set(index) == {'title', 'updated', 'metrics', 'settings', 'custom_alert_cutoffs'}
    assert len(index['metrics']) == 55
    assert sum(x['type'].startswith('Scored') for x in index['metrics']) == 19
    assert index['custom_alert_cutoffs']['fields'] == 8
    actual = {}
    for name in ('structural_v9', 'low_information_v9', 'correlation_v9', 'outliers_v9',
                 'qc_reproducibility_v9', 'labels_v9', 'separability_v9', 'readiness_v9'):
        for node in ast.parse((MODULES / (name + '.py')).read_text()).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == 'PARAMETERS' for t in targets):
                    actual.update(ast.literal_eval(node.value))
    assert len(actual) == len(index['settings']) == len(LABELS) == 13
    rows = {row['setting']: row for row in index['settings']}
    for key, (default, low, high, step) in actual.items():
        row = rows[LABELS[key]]
        assert float(row['default']) == default, key
        assert [float(v) for v in row['allowed_range'].split('–')] == [low, high], key
        assert float(row['step']) == step, key
    assert (MANUAL / 'MERIT-ML_User_Manual.pdf').read_bytes().startswith(b'%PDF-')
    print('PASS: reader-facing manual; 19 scored items; 13 current settings; eight custom cut-offs.')

if __name__ == '__main__':
    main()
