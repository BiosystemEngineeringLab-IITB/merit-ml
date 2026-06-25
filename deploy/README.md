# MERIT Cloudflare + Vercel Deploy Kit

This folder contains the operational deployment toolkit for MERIT-ML.

For the full step-by-step runbook, use:

[MERIT_DEPLOYMENT_RUNBOOK.md](MERIT_DEPLOYMENT_RUNBOOK.md)

For future Codex/agent sessions, read this first when the user asks to push,
deploy, rebuild Docker, update R2, or make local/remote MERIT-ML releases match:

[AGENT_RELEASE_AUTOMATION.md](AGENT_RELEASE_AUTOMATION.md)

That runbook covers:

1. Uploading local precomputed MERIT cache updates to Cloudflare R2.
2. Updating Vercel production environment variables.
3. Deploying UI/API updates to Vercel production.
4. Keeping the Docker image current for GitHub Container Registry and DockerHub.
5. Updating release-safe GitHub repository files.
6. Smoke-testing the hosted app and local Docker image.

Additional focused guides:

- [VERCEL_CICD_MERIT.md](VERCEL_CICD_MERIT.md) records the MERIT-specific Vercel deployment, promote, rollback, inspect, and CI/CD workflow.
- [GITHUB_PUSH_AUTOMATION.md](GITHUB_PUSH_AUTOMATION.md) records one-time deploy-key setup and non-interactive GitHub push instructions.
- [github_release_push.sh](github_release_push.sh) syncs only release-safe files to the public `BiosystemEngineeringLab-IITB/merit-ml` repository.

The public Docker image is a thin UI/runtime container. It must not include
`merit-cache-workbench-full-v7/` or local Metabolomics Workbench dump folders.
It reads hosted MERIT assessment artifacts at runtime and uses live
Metabolomics Workbench REST calls for MERIT-derived tabular exports.

## Quick Start

From the repository root:

```bash
cd /home/shayantan/metabolomics/ML-ready
```

### UI-only Vercel deploy

Use this when only UI code changed and the R2 cache location is unchanged:

```bash
./deploy/deploy.sh --skip-sync --skip-vercel-env
```

### Surgical v7 cache update to R2

Use this when local cache JSON/index files changed but the Vercel app already points to the same R2 prefix:

```bash
./deploy/deploy.sh \
  --cache-root /home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7 \
  --r2-prefix merit-cache/v7 \
  --skip-vercel-env \
  --skip-deploy
```

### Cache update plus Vercel env/deploy

Use this only when intentionally changing the public R2 cache prefix:

```bash
./deploy/deploy.sh \
  --cache-root /home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7 \
  --r2-prefix merit-cache/v7-$(date +%F-%H%M)
```

### Docker rebuild and DockerHub push

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
docker tag merit-ml:v7-local banerjee28/merit-ml:v7
docker tag merit-ml:v7-local banerjee28/merit-ml:latest
docker push banerjee28/merit-ml:v7
docker push banerjee28/merit-ml:latest
```

### Docker rebuild and GHCR push

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
docker tag merit-ml:v7-local ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker tag merit-ml:v7-local ghcr.io/biosystemengineeringlab-iitb/merit-ml:latest
docker push ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker push ghcr.io/biosystemengineeringlab-iitb/merit-ml:latest
```

### Release-safe GitHub push

Use this when the public GitHub repository should be updated without exposing
local caches, raw dumps, credentials, or private deployment files:

```bash
./deploy/github_release_push.sh --dry-run --message "Describe the MERIT-ML release change"
./deploy/github_release_push.sh --message "Describe the MERIT-ML release change"
```

If GitHub authentication is not yet persistent on this server, follow
[GITHUB_PUSH_AUTOMATION.md](GITHUB_PUSH_AUTOMATION.md) once to add a
write-enabled repository deploy key.

## One-Time Local Setup

Create runtime config files from templates:

```bash
cp deploy/credentials.env.template deploy/credentials.env
cp deploy/config.env.template deploy/config.env
chmod 600 deploy/credentials.env deploy/config.env
```

Edit:

- `deploy/credentials.env` with Cloudflare R2 S3-compatible credentials.
- `deploy/config.env` with the R2 endpoint, bucket, public root, default prefix, and Vercel settings.

## Required CLIs

```bash
aws --version
vercel --version
docker --version
git --version
```

If not logged in to Vercel:

```bash
vercel login
vercel link
```

## Security Notes

- Never commit `deploy/credentials.env` or `deploy/config.env`.
- Never commit token scratch files.
- Rotate R2, GitHub, or DockerHub tokens if they were exposed in a file, terminal, chat, or commit.
- Keep `chmod 600` on local credential/config files.
