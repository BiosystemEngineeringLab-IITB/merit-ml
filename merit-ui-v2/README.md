# merit-ui-v2

MERIT UI v2 sandbox and current deployed UI.

Changes relative to `merit-local-v1`:

- Diplomatic readiness-band display labels:
  - Ready -> ML-ready
  - Conditional -> ML-ready with caveats
  - Fragile -> Exploratory ML use
  - Not Ready -> Class-support limited
  - No Data -> Metadata-only record
- Left workflow stepper removed and replaced with a tunable scoring-parameter panel.
- Parameter changes are applied server-side on reload; cached v7 JSON files are not edited.
- The v2 renderer recalculates directly threshold-dependent metric scores, section scores, gates, final bands, and tooltip text from the selected parameter profile.

Run this folder separately from the current local UI so `merit-local-v1` and the root UI remain untouched.

Deployment record:

- Date: 2026-05-04
- Domain: `https://www.merit-ml.in`
- Vercel deployment id: `dpl_9zK6LZ27MEs6qS2k4o8e3F8e94bH`
- Deployment URL: `https://merit-ml-ready-oq89azktc-shayantan-banerjees-projects.vercel.app`
- Remote/local v2 diff artifact: `outputs/ui_v2_remote_diff_20260504T_supervised_scope/summary.json`

Terminology: v2 displays `ML-eligible sample count` for QC/blank-filtered samples used by ML metrics and `Label Structure and Class Support` for the former cohort/class-balance section. Internal cache keys are unchanged.

## Local Docker package

The repository root includes a Docker packaging recipe that runs this UI with the bundled v7 precomputed cache:

```bash
docker compose -f docker-compose.merit.yml build
docker compose -f docker-compose.merit.yml up
```

Open `http://localhost:8773` after the container starts. Cached readiness reports are served locally; the "Download Tabular Data" feature still fetches matrices live from Metabolomics Workbench REST endpoints and therefore requires internet access.

See `docker/README.md` for GitHub Container Registry and GitHub Release distribution options.
