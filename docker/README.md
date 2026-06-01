# MERIT Local Docker Package

This package builds a local MERIT web UI image that includes the current precomputed Workbench assessment cache. Users can run the UI in their own browser without depending on the hosted MERIT site.

The readiness reports are served from the bundled cache. The **Download Tabular Data** feature still fetches tabular matrices live from Metabolomics Workbench REST endpoints, so that feature requires an internet connection.

## Build Locally

Run from the `ML-ready` repository root:

```bash
docker compose -f docker-compose.merit.yml build
```

This creates:

```text
merit-ml:v7-local
```

The Docker build context is intentionally restricted by `.dockerignore` to:

- `merit-ui-v2/`
- `merit-cache-workbench-full-v7/json/`
- `merit-cache-workbench-full-v7/index.json`
- `merit-cache-workbench-full-v7/study_metadata_index.json`
- `merit-cache-workbench-full-v7/citation_index.json`

Older caches, manuscript files, figures, scratch files, and local dumps are excluded.

## Run Locally

```bash
docker compose -f docker-compose.merit.yml up
```

Then open:

```text
http://localhost:8773
```

To run without compose:

```bash
docker run --rm -p 8773:8773 merit-ml:v7-local
```

## Share Through GitHub

Do not commit the multi-GB cache as normal Git files. Use one of these release paths instead.

### Recommended: GitHub Container Registry

Build once, then publish the image:

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
docker tag merit-ml:v7-local ghcr.io/<OWNER>/<REPO>/merit-ml:v7
docker push ghcr.io/<OWNER>/<REPO>/merit-ml:v7
```

Users can then run:

```bash
docker pull ghcr.io/<OWNER>/<REPO>/merit-ml:v7
docker run --rm -p 8773:8773 ghcr.io/<OWNER>/<REPO>/merit-ml:v7
```

### Alternative: GitHub Release Asset

Export the image as a compressed artifact:

```bash
docker save merit-ml:v7-local | gzip > merit-ml-v7-local-docker-image.tar.gz
```

Attach the `.tar.gz` file to a GitHub Release. Users can load it with:

```bash
docker load < merit-ml-v7-local-docker-image.tar.gz
docker run --rm -p 8773:8773 merit-ml:v7-local
```

## Notes For Users

- The container serves cached MERIT readiness reports locally.
- The browser opens on the user's own system at `http://localhost:8773`.
- The **Download Tabular Data** tab performs live Workbench REST API calls and needs internet access.
- MERIT does not write new cache files inside the container during normal UI use.
