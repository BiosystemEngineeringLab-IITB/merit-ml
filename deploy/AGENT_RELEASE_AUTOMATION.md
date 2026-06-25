# MERIT-ML Agent Release Automation

This file is the first file to read when a future Codex/agent session is asked to push, deploy, publish, rebuild Docker, update Vercel, update R2, or sync GitHub for MERIT-ML.

The goal is to make release work routine, conservative, and reproducible. Do not improvise deployment steps until this file and `deploy/MERIT_DEPLOYMENT_RUNBOOK.md` have been checked.

## Required Agent Behavior

When the user asks for any of these phrases or equivalent intent:

- push to GitHub
- update the GitHub repository
- deploy to Vercel
- update the remote UI
- push to remote
- update Docker image
- rebuild Docker
- update DockerHub or GHCR
- update R2
- sync cache
- make local and remote UI match

then the agent should automatically do the following:

1. Read this file.
2. Read `deploy/MERIT_DEPLOYMENT_RUNBOOK.md`.
3. If Vercel deployment is involved, apply the Vercel CI/CD notes in `deploy/VERCEL_CICD_MERIT.md`.
4. Inspect the working tree and identify the exact intended scope.
5. Choose the smallest safe release path from the matrix below.
6. Run only the commands for that path.
7. Verify the result.
8. Report URLs, image tags/digests, commit SHA, and any skipped step.

Do not ask the user to repeat these instructions. They live here so future sessions can recover context automatically.

## Change-Type Routing Matrix

| User request or change type | Required action | Commands/doc to use |
|---|---|---|
| UI text, layout, routes, result page behavior | Vercel deploy; Docker rebuild/push; GitHub sync if public repo should match | `deploy/deploy.sh --skip-sync --skip-vercel-env`; `deploy/github_release_push.sh`; Docker commands in `MERIT_DEPLOYMENT_RUNBOOK.md` |
| Public README, Docker instructions, deploy docs only | GitHub sync only | `deploy/github_release_push.sh` |
| Dockerfile or compose change | Docker rebuild/push and GitHub sync | Docker commands in `MERIT_DEPLOYMENT_RUNBOOK.md`; `deploy/github_release_push.sh` |
| Local cache JSON/index/citation/study-design correction only | R2 sync only when using same prefix; no Docker unless runtime changed | `deploy/deploy.sh --cache-root ... --r2-prefix merit-cache/v7 --skip-vercel-env --skip-deploy` |
| New public cache prefix/version | R2 sync plus Vercel env update plus Vercel deploy | `deploy/deploy.sh --cache-root ... --r2-prefix <new-prefix>` |
| Download/export logic change | Vercel deploy; Docker rebuild/push; GitHub sync | UI deploy path plus Docker and GitHub |
| Manuscript figures, analysis outputs, scratch files | Usually no public deployment | Do not sync unless explicitly requested |

## Default Release Order

For an end-to-end UI/runtime release, use this order:

1. Verify local source change.
2. Run lightweight syntax or health checks.
3. Deploy Vercel production UI if the hosted UI changed.
4. Rebuild and smoke-test Docker image if the public local UI should match.
5. Push DockerHub/GHCR image tags if the Docker image changed.
6. Sync release-safe files to GitHub using `deploy/github_release_push.sh`.
7. Verify GitHub remote contains the expected commit.

For cache-only updates, use this order:

1. Update local cache files.
2. Sanity-check the affected study/studies locally.
3. Sync only the stable R2 prefix unless deliberately publishing a new cache version.
4. Do not rebuild Docker and do not redeploy Vercel if the app already points to the same R2 prefix.

## Hard Safety Rules

Never copy or publish these to GitHub, Docker, or Vercel build output:

- `merit-cache-workbench-full-v7/`
- `merit-cache-workbench-full-v*/`
- `mw-dump-latest-confirmation*/`
- `merit-cache-metabolights-fetch-v1/`
- local REST dumps or raw repository mirrors
- manuscript scratch outputs unless explicitly selected for public documentation
- `deploy/credentials.env`
- `deploy/config.env`
- `deploy/cloudfare` or `deploy/cloudflare`
- token files, secret files, private keys, `.vercel/project.json`

The public Docker image must remain a thin UI/runtime image. It should fetch compact MERIT-derived assessment artifacts from the hosted endpoint/R2 at runtime and use live Metabolomics Workbench REST calls for source-specific exports.

## GitHub Push Automation

Use:

```bash
cd /home/shayantan/metabolomics/ML-ready
./deploy/github_release_push.sh --message "Describe the MERIT-ML release change"
```

Recommended first pass:

```bash
./deploy/github_release_push.sh --dry-run --message "Describe the MERIT-ML release change"
```

If authentication is not configured, set up the repository deploy key once using `deploy/GITHUB_PUSH_AUTOMATION.md`. After that, future pushes should not require login prompts.

## Vercel Deployment Rules

Use the project deploy script for MERIT rather than ad-hoc Vercel commands unless debugging:

```bash
./deploy/deploy.sh --skip-sync --skip-vercel-env
```

This is the default UI-only production deploy path.

Use direct Vercel commands only when the deploy script is inappropriate or when following `deploy/VERCEL_CICD_MERIT.md` for inspect/promote/rollback/prebuilt workflows.

## Verification Checklist

After a UI/runtime release, verify:

```bash
curl -fsSL https://www.merit-ml.in/healthz
curl -fsSL https://www.merit-ml.in/ | head
```

For representative study pages, check at least one URL manually or with curl:

```text
https://www.merit-ml.in/?study_id=ST000043
https://www.merit-ml.in/?study_id=ST000496
https://www.merit-ml.in/?study_id=ST001518
https://www.merit-ml.in/?study_id=ST003741
```

After a Docker release, verify:

```bash
docker pull banerjee28/merit-ml:v7
docker run --rm -d --name merit-ml-smoke -p 8780:8773 banerjee28/merit-ml:v7
curl -fsSL http://localhost:8780/healthz
docker rm -f merit-ml-smoke
```

After a GitHub release, verify:

```bash
git ls-remote https://github.com/BiosystemEngineeringLab-IITB/merit-ml.git refs/heads/main
```

## Final Response Format

When finished, report:

- What changed.
- Whether Vercel was deployed, with URL/status.
- Whether Docker was rebuilt/pushed, with tags/digest if available.
- Whether GitHub was pushed, with commit SHA.
- Whether R2 was touched. If not, explicitly say R2 was not touched.
- Any verification performed.
