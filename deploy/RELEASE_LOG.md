# MERIT-ML Release Log

This file records operational release details that future agents or maintainers can
use to reproduce targeted MERIT-ML deployments without relying on chat context.

## 2026-08-17 Subject-Level Repeated-Sample Fix

### Purpose

This release fixes a subject-level sample-count issue first observed for
`ST000087`, where a deposited matrix had 79 matrix rows but many rows mapped to
the same explicit subject-level identifier in mwTab `SUBJECT_SAMPLE_FACTORS`.

The fix keeps matrix row IDs unchanged, but when explicit high-confidence
patient/subject/donor/participant-like IDs repeat across rows, MERIT-ML now:

- reports repeated subject-level rows in the Structural `duplicate_entities`
  metric;
- uses effective independent biological units for the G2 sample-count gate;
- displays a non-scoring Overview note: `Repeated subject-level rows detected`.

### High-Confidence Affected Studies

Only these 9 studies were recomputed for the local v7 cache:

| Study ID | New score | New band | Effective units / ML-eligible rows |
|---|---:|---|---:|
| ST000061 | 0.987 | Ready | 59 / 118 |
| ST000081 | 0.967 | Ready | 59 / 118 |
| ST000087 | 0.916 | Conditional | 17 / 79 |
| ST000089 | 0.886 | Not Ready | 7 / 46 |
| ST001369 | 0.819 | Conditional | 22 / 44 |
| ST002057 | 0.963 | Conditional | 12 / 103 |
| ST003108 | 0.859 | Not Ready | 6 / 72 |
| ST003849 | 0.766 | Not Ready | 2 / 108 |
| ST004037 | 0.702 | Conditional | 14 / 777 |

### Local Cache Files Updated

The targeted local v7 cache update changed exactly:

- `json/st000061_workflow_state.json`
- `json/st000061_readiness_score.json`
- `json/st000081_workflow_state.json`
- `json/st000081_readiness_score.json`
- `json/st000087_workflow_state.json`
- `json/st000087_readiness_score.json`
- `json/st000089_workflow_state.json`
- `json/st000089_readiness_score.json`
- `json/st001369_workflow_state.json`
- `json/st001369_readiness_score.json`
- `json/st002057_workflow_state.json`
- `json/st002057_readiness_score.json`
- `json/st003108_workflow_state.json`
- `json/st003108_readiness_score.json`
- `json/st003849_workflow_state.json`
- `json/st003849_readiness_score.json`
- `json/st004037_workflow_state.json`
- `json/st004037_readiness_score.json`
- `index.json`
- `study_metadata_index.json`

Do not broad-sync the entire cache for this fix. Use targeted R2 uploads only.

### Targeted Cloudflare R2 Upload

Upload exactly the 20 files above to both the stable v7 prefix and the active
production prefix when production still points to the older release prefix.

```bash
cd /home/shayantan/metabolomics/ML-ready

set -a
source deploy/config.env
source deploy/credentials.env
set +a
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

for prefix in \
  merit-cache/v7 \
  merit-cache/releases/v7.2026-04-30-190939.metabatch-annotation-compatibility
do
  for rel in index.json study_metadata_index.json; do
    aws s3 cp "merit-cache-workbench-full-v7/${rel}" \
      "s3://${R2_BUCKET}/${prefix}/${rel}" \
      --endpoint-url "${R2_ENDPOINT}" \
      --only-show-errors
  done

  for sid in st000061 st000081 st000087 st000089 st001369 st002057 st003108 st003849 st004037; do
    for suffix in workflow_state readiness_score; do
      rel="json/${sid}_${suffix}.json"
      aws s3 cp "merit-cache-workbench-full-v7/${rel}" \
        "s3://${R2_BUCKET}/${prefix}/${rel}" \
        --endpoint-url "${R2_ENDPOINT}" \
        --only-show-errors
    done
  done
done
```

Verify remote parity through the S3 API rather than the public R2 URL, because
direct public object fetches can return 403 depending on bucket configuration.

```bash
tmpdir=/tmp/merit-r2-targeted-verify
rm -rf "${tmpdir}" && mkdir -p "${tmpdir}"
prefix=merit-cache/releases/v7.2026-04-30-190939.metabatch-annotation-compatibility

rels=(index.json study_metadata_index.json)
for sid in st000061 st000081 st000087 st000089 st001369 st002057 st003108 st003849 st004037; do
  rels+=("json/${sid}_workflow_state.json" "json/${sid}_readiness_score.json")
done

for rel in "${rels[@]}"; do
  mkdir -p "${tmpdir}/$(dirname "${rel}")"
  aws s3 cp "s3://${R2_BUCKET}/${prefix}/${rel}" "${tmpdir}/${rel}" \
    --endpoint-url "${R2_ENDPOINT}" \
    --only-show-errors
  sha256sum "merit-cache-workbench-full-v7/${rel}" "${tmpdir}/${rel}"
done
```

### Vercel Deployment

Production deployment completed:

- production domain: `https://merit-ml.in`
- deployment id: `dpl_AEYnvi4mHtxh1FD1XoDQ5NQpXFXf`
- deployment URL:
  `https://merit-ml-ready-79d3iyan7-shayantan-banerjees-projects.vercel.app`

Default deploy command:

```bash
./deploy/deploy.sh --skip-sync --skip-vercel-env
```

### Docker Release

The public Docker image must stay thin and must not include
`merit-cache-workbench-full-v7/` or local raw Metabolomics Workbench dumps.
The current `.dockerignore` excludes everything except the UI/runtime allowlist.

Build and push:

```bash
cd /home/shayantan/metabolomics/ML-ready

sudo docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
sudo docker tag merit-ml:v7-local banerjee28/merit-ml:v7
sudo docker tag merit-ml:v7-local banerjee28/merit-ml:latest
sudo docker push banerjee28/merit-ml:v7
sudo docker push banerjee28/merit-ml:latest
```

Optional GHCR push if credentials are available:

```bash
sudo docker tag merit-ml:v7-local ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
sudo docker tag merit-ml:v7-local ghcr.io/biosystemengineeringlab-iitb/merit-ml:latest
sudo docker push ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
sudo docker push ghcr.io/biosystemengineeringlab-iitb/merit-ml:latest
```

Smoke test:

```bash
sudo docker rm -f merit-ml-smoke 2>/dev/null || true
sudo docker run --rm -d --name merit-ml-smoke -p 8780:8773 banerjee28/merit-ml:v7
curl -fsSL http://localhost:8780/healthz
curl -fsSL 'http://localhost:8780/?study_id=ST000087' | grep -q 'Repeated subject-level rows detected'
sudo docker rm -f merit-ml-smoke
```

### GitHub Release Sync

Use the release-safe GitHub sync script. It copies only whitelisted public files
and excludes cache folders, raw dumps, credentials, local config, and private
deployment files.

```bash
./deploy/github_release_push.sh --dry-run --message "Add subject-level repeated-sample detection"
./deploy/github_release_push.sh --message "Add subject-level repeated-sample detection"
```

### Verification Markers

After release, verify:

- `https://www.merit-ml.in/healthz` returns `ok`.
- `https://www.merit-ml.in/?study_id=ST000087` contains
  `Repeated subject-level rows detected`.
- `ST000087` contains `17 independent biological units`.
- `ST000087` contains score `91.6`, and old score `93.9` is absent.
- Unaffected control `ST000043` does not show the Overview repeated-subject
  note.

### 2026-08-17 Execution Record

- Cloudflare R2 targeted refresh completed for both prefixes:
  - `merit-cache/v7`
  - `merit-cache/releases/v7.2026-04-30-190939.metabatch-annotation-compatibility`
- Active production-prefix parity verified by SHA256 through the S3-compatible
  API for all 20 targeted files.
- DockerHub image updated:
  - `banerjee28/merit-ml:v7`
  - `banerjee28/merit-ml:latest`
  - digest: `sha256:6d83a3ea3a42fe59ed37a502f75385b737e9a93b859675c54f1bcbbe184ac1fd`
  - local image size: `165955103` bytes
- Docker smoke test passed on `http://localhost:8791`:
  - `/healthz` returned `ok`
  - `ST000087` contained `Repeated subject-level rows detected`
  - `ST000087` contained `17 independent biological units`
  - `ST000087` contained score `91.6`
  - old score `93.9` was absent
