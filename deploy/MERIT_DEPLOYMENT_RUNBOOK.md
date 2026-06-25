# MERIT-ML Deployment Runbook

This runbook records the operational steps for keeping the MERIT-ML hosted UI, Cloudflare R2 cache, GitHub/GHCR image, and DockerHub image in sync.

Use this from the repository root unless a command explicitly says otherwise:

```bash
cd /home/shayantan/metabolomics/ML-ready
```

## Agent/Future-Session Entry Point

When a future Codex/agent session is asked to push, deploy, rebuild Docker,
update R2, update Vercel, or sync the public GitHub repository, it should first
read:

```text
deploy/AGENT_RELEASE_AUTOMATION.md
```

That file maps common user requests to the smallest safe release path and points
to the exact commands to run. For Vercel-specific deployment, promote, rollback,
inspect, logs, or CI/CD commands, use:

```text
deploy/VERCEL_CICD_MERIT.md
```

For non-interactive GitHub push setup and release-safe GitHub sync, use:

```text
deploy/GITHUB_PUSH_AUTOMATION.md
deploy/github_release_push.sh
```

## 0. Current Deployment Model

MERIT-ML has two public delivery modes:

1. Hosted web app: `https://www.merit-ml.in/`
2. Local Docker app: users run the same UI locally in a browser using a Docker image.

The hosted Vercel app is intentionally lightweight. It does not bundle the full precomputed cache. Instead, it reads precomputed JSON artifacts from Cloudflare R2 through environment variables.

The public Docker image is also intentionally lightweight. It packages the local UI/runtime only and does **not** bundle `merit-cache-workbench-full-v7/` or local Metabolomics Workbench source dumps. The container reads hosted MERIT-derived assessment artifacts from the configured R2 root at runtime. The MERIT-derived tabular export workflow fetches matrices live from Metabolomics Workbench REST endpoints, so Docker users need internet access for normal study lookup and export generation.

## 1. Important Paths

| Purpose | Path |
|---|---|
| Repository root | `/home/shayantan/metabolomics/ML-ready` |
| Current UI source used by Vercel and Docker | `merit-ui-v2/` |
| Vercel entrypoint | `api/index.py` or `merit-ui-v2/api/index.py` depending on deployment root |
| Root Vercel config | `vercel.json` |
| Current local cache | `merit-cache-workbench-full-v7/` |
| Cache JSON files | `merit-cache-workbench-full-v7/json/` |
| Study browser index | `merit-cache-workbench-full-v7/study_metadata_index.json` |
| Citation index | `merit-cache-workbench-full-v7/citation_index.json` |
| Dockerfile | `docker/merit-ui-v2.Dockerfile` |
| Docker Compose file | `docker-compose.merit.yml` |
| R2/Vercel deploy script | `deploy/deploy.sh` |
| R2/Vercel config template | `deploy/config.env.template` |
| R2 credential template | `deploy/credentials.env.template` |

## 2. Required CLIs

Check these before deploying:

```bash
aws --version
vercel --version
docker --version
git --version
```

Optional but useful:

```bash
gh --version
```

If Vercel is not linked yet:

```bash
vercel login
vercel link
```

If Docker requires `sudo` and you want to avoid typing it every time, add your user to the Docker group and start a new shell/session:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

## 3. Security Rules

Never commit real credentials.

Keep these files local only:

```text
deploy/credentials.env
deploy/config.env
deploy/cloudfare
deploy/cloudflare
deploy/*.token
deploy/*secret*
deploy/*credential*
```

If any Cloudflare R2 token, GitHub PAT, or DockerHub token was pasted into a file, terminal, chat, or repository by mistake, rotate it immediately.

Use environment variables or local ignored files for secrets:

```bash
export GITHUB_PAT='...'
export DOCKERHUB_TOKEN='...'
```

Do not put tokens directly into commands that will be saved in shell history.

## 4. Pre-Deployment Sanity Checks

Run these before any cache, UI, or Docker release.

### 4.1 Check working tree

```bash
git status --short
```

Review unexpected changes before deploying.

### 4.2 Check cache shape

```bash
test -f merit-cache-workbench-full-v7/index.json
test -f merit-cache-workbench-full-v7/study_metadata_index.json
test -f merit-cache-workbench-full-v7/citation_index.json
test -d merit-cache-workbench-full-v7/json
find merit-cache-workbench-full-v7/json -maxdepth 1 -name '*.json' | wc -l
```

Expected current JSON count is approximately 8,242 files because the cache contains workflow-state and readiness-score JSON artifacts.

### 4.3 Check Vercel exclude rules

The hosted Vercel app should not upload the large local cache. Confirm `.vercelignore` excludes local cache directories:

```bash
rg -n 'merit-cache-workbench-full-v\*|deploy/credentials.env|deploy/config.env' .vercelignore
```

## 5. Cloudflare R2 Cache Deployment

The hosted app reads precomputed results from R2 using:

```text
MERIT_PRECOMPUTED_ROOT=<public R2 cache root>
MERIT_CACHE_ONLY=1
```

The UI resolves cache roots in this priority order:

```text
MERIT_UI_PRECOMPUTED_ROOT > MERIT_PRECOMPUTED_ROOT > MERIT_CACHE_BASE_URL > local merit-cache-workbench-full-v7
```

### 5.1 One-time local config

Create local config files if they do not already exist:

```bash
cp deploy/config.env.template deploy/config.env
cp deploy/credentials.env.template deploy/credentials.env
chmod 600 deploy/config.env deploy/credentials.env
```

Edit them locally. Do not commit them.

### 5.2 Preferred mode for surgical v7 cache updates

Use a stable R2 prefix when only the existing v7 cache has been surgically updated, for example study-design label corrections, citation index updates, or corrected per-study JSON files.

This avoids creating another multi-GB R2 copy.

Example stable-prefix upload only:

```bash
./deploy/deploy.sh \
  --cache-root /home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7 \
  --r2-prefix merit-cache/v7 \
  --skip-vercel-env \
  --skip-deploy
```

Use this when the Vercel environment already points to the same public R2 prefix. The remote UI will read the updated objects from the same location.

### 5.3 New-version cache release mode

Use a new dated/versioned prefix only when intentionally publishing a new cache version and preserving the old one is scientifically useful.

Example:

```bash
./deploy/deploy.sh \
  --cache-root /home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7 \
  --r2-prefix merit-cache/v7-$(date +%F-%H%M)
```

This does three things:

1. Syncs local cache files to R2.
2. Updates Vercel production environment variables.
3. Deploys the Vercel production app.

Do not use dated prefixes for routine surgical updates, otherwise R2 storage will grow unnecessarily.

### 5.4 Exact mirror cleanup, if needed

`deploy/deploy.sh` uses `aws s3 sync` without `--delete`, which is safer for routine deployments. If you intentionally need the R2 prefix to exactly mirror the local cache, do a dry run first:

```bash
source deploy/config.env
source deploy/credentials.env
aws s3 sync merit-cache-workbench-full-v7 "s3://${R2_BUCKET}/merit-cache/v7/" \
  --endpoint-url "${R2_ENDPOINT}" \
  --delete \
  --dryrun
```

If the dry run looks correct, remove `--dryrun`.

Only use `--delete` when you are sure the prefix is dedicated to the current cache.

### 5.5 R2 verification

Check that the index exists:

```bash
source deploy/config.env
source deploy/credentials.env
aws s3 ls "s3://${R2_BUCKET}/merit-cache/v7/index.json" \
  --endpoint-url "${R2_ENDPOINT}"
```

Check approximate R2 size under a prefix:

```bash
aws s3 ls "s3://${R2_BUCKET}/merit-cache/v7/" \
  --endpoint-url "${R2_ENDPOINT}" \
  --recursive \
  --summarize \
  --human-readable
```

Check public access using the configured public root:

```bash
curl -fsSL "${R2_PUBLIC_ROOT%/}/merit-cache/v7/index.json" | python3 -m json.tool >/tmp/merit-r2-index-check.json
```

## 6. Vercel UI Deployment

### 6.1 UI-only update

Use this when only UI code changed and R2 cache location is unchanged:

```bash
./deploy/deploy.sh --skip-sync --skip-vercel-env
```

Equivalent direct command:

```bash
vercel --prod --yes
```

The deploy script is preferred because it prints the deployment URL and health check.

### 6.2 Cache-root update plus UI deployment

Use this when the Vercel app must point to a new R2 prefix:

```bash
./deploy/deploy.sh \
  --cache-root /home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7 \
  --r2-prefix merit-cache/v7-$(date +%F-%H%M)
```

### 6.3 Vercel environment variables

The production app should have:

```text
MERIT_PRECOMPUTED_ROOT=<public R2 URL ending in />
MERIT_CACHE_ONLY=1
```

Check environment variables with:

```bash
vercel env ls production
```

To update manually:

```bash
vercel env rm MERIT_PRECOMPUTED_ROOT production --yes
printf '%s\n' 'https://<public-r2-root>/merit-cache/v7/' | vercel env add MERIT_PRECOMPUTED_ROOT production

vercel env rm MERIT_CACHE_ONLY production --yes
printf '%s\n' '1' | vercel env add MERIT_CACHE_ONLY production
```

Then redeploy:

```bash
vercel --prod --yes
```

### 6.4 Vercel smoke tests

After deployment:

```bash
curl -fsSL https://www.merit-ml.in/healthz
curl -I https://www.merit-ml.in/
```

Open the browser and test representative studies:

```text
https://www.merit-ml.in/?study_id=ST000149
https://www.merit-ml.in/?study_id=ST000496
https://www.merit-ml.in/?study_id=ST003494
```

Recommended UI checks:

- Landing page loads without hanging.
- Study report loads from cache.
- Source tabs render correctly.
- Annotation pie chart renders where applicable.
- Study-design context disclaimer appears where expected.
- Download Result JSON works.
- Download Tabular Data tab performs live Workbench fetches.
- Bulk MERIT analysis loads and exports TSV/JSON as expected.

## 7. Docker Image Update

Docker is used for local browser deployment by end users. The image includes:

- `merit-ui-v2/` runtime code and static assets
- a default `MERIT_UI_PRECOMPUTED_ROOT` pointing to the hosted MERIT assessment artifact root

The image must exclude:

- `merit-cache-workbench-full-v7/`
- local Metabolomics Workbench dump folders
- generated MERIT export ZIPs
- manuscript outputs, figures, scratch folders, and private deployment credentials

This is enforced through `.dockerignore` and by avoiding any `COPY merit-cache-workbench-full-v7 ...` line in `docker/merit-ui-v2.Dockerfile`.

### 7.1 Build local Docker image

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
```

Or with compose:

```bash
docker compose -f docker-compose.merit.yml build
```

### 7.2 Local Docker smoke test

```bash
docker rm -f merit-ml 2>/dev/null || true
docker run -d --name merit-ml -p 8780:8773 merit-ml:v7-local
curl -fsSL http://localhost:8780/healthz
```

Open:

```text
http://localhost:8780
```

Stop after testing:

```bash
docker stop merit-ml
docker rm merit-ml
```

### 7.3 Check image size

```bash
docker images merit-ml:v7-local
```

Confirm that the v7 cache is not bundled:

```bash
docker run --rm merit-ml:v7-local sh -lc \
  'find /opt/merit -maxdepth 4 -iname "*merit-cache*" -o -iname "*mw-dump*"'
```

The command should not print a bundled Workbench cache directory.

For a detailed size breakdown:

```bash
docker history merit-ml:v7-local
```

## 8. Publish Docker Image to GitHub Container Registry

Current GHCR image name:

```text
ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

Login:

```bash
echo "$GITHUB_PAT" | docker login ghcr.io -u <github-username> --password-stdin
```

Build and tag:

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
docker tag merit-ml:v7-local ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker tag merit-ml:v7-local ghcr.io/biosystemengineeringlab-iitb/merit-ml:latest
```

Push:

```bash
docker push ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker push ghcr.io/biosystemengineeringlab-iitb/merit-ml:latest
```

Test pull on another machine:

```bash
docker pull ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker run --rm -p 8780:8773 ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

Open:

```text
http://localhost:8780
```

If pull is denied, check GitHub package visibility and token permissions. For public users, the GHCR package must be public or they must authenticate.

## 9. Publish Docker Image to DockerHub

Current DockerHub image name:

```text
banerjee28/merit-ml:v7
```

Login:

```bash
docker login
```

Or with a token:

```bash
echo "$DOCKERHUB_TOKEN" | docker login -u banerjee28 --password-stdin
```

Build and tag:

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
docker tag merit-ml:v7-local banerjee28/merit-ml:v7
docker tag merit-ml:v7-local banerjee28/merit-ml:latest
```

Push:

```bash
docker push banerjee28/merit-ml:v7
docker push banerjee28/merit-ml:latest
```

Test pull:

```bash
docker pull banerjee28/merit-ml:v7
docker run --rm -p 8780:8773 banerjee28/merit-ml:v7
```

Open:

```text
http://localhost:8780
```

## 10. Update GitHub Repository Content

Use this when the public GitHub repository needs README, Dockerfile, compose, manual, or deployment-documentation updates.

Repository:

```text
https://github.com/BiosystemEngineeringLab-IITB/merit-ml
```

Preferred automated workflow:

```bash
cd /home/shayantan/metabolomics/ML-ready
./deploy/github_release_push.sh --dry-run --message "Describe the MERIT-ML release change"
./deploy/github_release_push.sh --message "Describe the MERIT-ML release change"
```

The helper script clones or updates the public release checkout, copies only a
whitelist of release-safe files, refuses forbidden cache/credential paths, shows
the diff, commits with the supplied message, and pushes when GitHub
authentication is configured.

If GitHub authentication is not yet persistent on this server, follow:

```text
deploy/GITHUB_PUSH_AUTOMATION.md
```

Manual fallback workflow:

```bash
cd /tmp/merit-ml-release   # or wherever the public release repo is cloned
git status --short
git pull --rebase origin main
```

Copy only release-safe files from the working tree. Do not copy local credentials, old caches, manuscript scratch folders, or private outputs.

Example release-safe sync commands, adjust as needed:

```bash
rsync -a --delete /home/shayantan/metabolomics/ML-ready/docker/ ./docker/
rsync -a /home/shayantan/metabolomics/ML-ready/docker-compose.merit.yml ./docker-compose.merit.yml
rsync -a /home/shayantan/metabolomics/ML-ready/.dockerignore ./.dockerignore
rsync -a /home/shayantan/metabolomics/ML-ready/deploy/MERIT_DEPLOYMENT_RUNBOOK.md ./deploy/MERIT_DEPLOYMENT_RUNBOOK.md
```

If the public repo includes the user manual, copy the latest PDF/Markdown intentionally:

```bash
rsync -a /home/shayantan/metabolomics/ML-ready/merit/docs/merit_ml_user_manual_2026-06-02/ ./docs/merit_ml_user_manual_2026-06-02/
```

Commit and push:

```bash
git add README.md docker docker-compose.merit.yml .dockerignore deploy docs
git commit -m "Update MERIT-ML deployment and Docker instructions"
git push origin main
```

If Git reports dubious ownership for the release checkout:

```bash
git config --global --add safe.directory /tmp/merit-ml-release
```

If push is rejected as non-fast-forward:

```bash
git pull --rebase origin main
git push origin main
```

## 11. Which Deployment Path Should I Use?

| Change type | R2 update? | Vercel deploy? | Docker rebuild/push? | GitHub repo update? |
|---|---:|---:|---:|---:|
| UI-only code change | No | Yes | Yes, if local Docker should match | Yes |
| Study-design labels/cache JSON only | Yes | No, if same R2 prefix | No, unless UI/runtime changed | Optional |
| New cache prefix/version | Yes | Yes | Yes | Yes |
| README/manual/deploy docs only | No | No | No | Yes |
| Dockerfile/compose change | No | No | Yes | Yes |
| Download Tabular Data logic change | No | Yes | Yes | Yes |

## 12. Final Release Checklist

Before announcing a release:

```bash
# Hosted app
curl -fsSL https://www.merit-ml.in/healthz

# DockerHub image
docker pull banerjee28/merit-ml:v7
docker run --rm -d --name merit-ml-test -p 8780:8773 banerjee28/merit-ml:v7
curl -fsSL http://localhost:8780/healthz
docker rm -f merit-ml-test

# GHCR image, if public or authenticated
docker pull ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

Manual browser smoke tests:

- `https://www.merit-ml.in/?study_id=ST000149`
- `https://www.merit-ml.in/?study_id=ST000496`
- `http://localhost:8780/?study_id=ST000149` after Docker run

Confirm:

- Study loads.
- Study-design context note is visible where expected.
- Annotation plots render.
- Result JSON download works.
- Download Tabular Data works using live Workbench REST calls.
- No user-facing text mentions local paths, cache internals, `v7`, or `merit-ui-v2` unless intentionally part of version documentation.
