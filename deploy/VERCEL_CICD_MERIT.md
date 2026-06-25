# MERIT-ML Vercel Deployment and CI/CD Notes

This file adapts the Vercel deployment/CI guidance to the MERIT-ML project. Use it together with `deploy/AGENT_RELEASE_AUTOMATION.md` and `deploy/MERIT_DEPLOYMENT_RUNBOOK.md`.

## MERIT-ML Production Deployment Model

MERIT-ML production is hosted on Vercel and served at:

```text
https://www.merit-ml.in/
https://merit-ml.in/
```

The hosted app should be lightweight. It should not upload or bundle the local Workbench cache. The production app reads compact precomputed MERIT artifacts from Cloudflare R2 using environment variables.

## Default UI-Only Production Deploy

Use when only UI/runtime code changed and the R2 cache root is unchanged:

```bash
cd /home/shayantan/metabolomics/ML-ready
./deploy/deploy.sh --skip-sync --skip-vercel-env
```

This is preferred over direct `vercel --prod` because the MERIT deploy script prints the effective deployment configuration and runs a health check.

Equivalent direct command, for debugging only:

```bash
vercel deploy --prod --yes
```

## Cache-Prefix Production Deploy

Use only when intentionally changing the public R2 prefix:

```bash
./deploy/deploy.sh \
  --cache-root /home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7 \
  --r2-prefix merit-cache/v7-$(date +%F-%H%M)
```

For routine surgical updates to the stable v7 prefix, do not update the Vercel environment and do not redeploy unless UI code changed.

## Required Vercel Environment Variables

Production should have:

```text
MERIT_PRECOMPUTED_ROOT=<public Cloudflare R2 prefix URL ending in />
MERIT_CACHE_ONLY=1
```

Check with:

```bash
vercel env ls production
```

Manual update pattern:

```bash
vercel env rm MERIT_PRECOMPUTED_ROOT production --yes
printf '%s\n' 'https://<public-r2-root>/merit-cache/v7/' | vercel env add MERIT_PRECOMPUTED_ROOT production

vercel env rm MERIT_CACHE_ONLY production --yes
printf '%s\n' '1' | vercel env add MERIT_CACHE_ONLY production

vercel deploy --prod --yes
```

## Build Locally, Deploy Prebuilt Output

Use this only for custom CI or when the local build must be frozen before deploy:

```bash
vercel pull --yes --environment=production
vercel build --prod
vercel deploy --prebuilt --prod
```

In CI, use:

```bash
vercel pull --yes --environment=production --token="$VERCEL_TOKEN"
vercel build --prod --token="$VERCEL_TOKEN"
vercel deploy --prebuilt --prod --token="$VERCEL_TOKEN"
```

Required CI secrets:

```text
VERCEL_TOKEN
VERCEL_ORG_ID
VERCEL_PROJECT_ID
```

Do not commit these secrets.

## Promote and Rollback

Promote a validated preview deployment without rebuilding:

```bash
vercel promote <deployment-url-or-id>
```

Rollback production:

```bash
vercel rollback
```

Rollback to a specific deployment:

```bash
vercel rollback <deployment-url-or-id>
```

Use promote/rollback when the exact deployment artifact matters.

## Inspect and Logs

List deployments:

```bash
vercel ls
```

Inspect one deployment:

```bash
vercel inspect <deployment-url-or-id>
```

Read logs:

```bash
vercel logs <deployment-url-or-id>
vercel logs <deployment-url-or-id> --follow
```

## Post-Deploy Verification

Always run:

```bash
curl -fsSL https://www.merit-ml.in/healthz
curl -I https://www.merit-ml.in/
```

Then check representative study URLs:

```text
https://www.merit-ml.in/?study_id=ST000043
https://www.merit-ml.in/?study_id=ST000496
https://www.merit-ml.in/?study_id=ST001518
https://www.merit-ml.in/?study_id=ST003741
```

## Deploy Result Reporting Template

Use this concise result block in final responses:

```text
Deploy result
- URL: <deployment-url or production domain>
- Target: production
- Status: READY or ERROR
- Commit: <short-sha if applicable>
- Verification: <health/study/docker checks>
- R2 touched: yes/no
- Docker touched: yes/no
- GitHub touched: yes/no
```
