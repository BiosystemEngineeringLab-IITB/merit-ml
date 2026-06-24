# MERIT-ML Local Docker Package

This Docker package runs the MERIT-ML browser interface locally without bundling the Metabolomics Workbench v7 cache inside the image.

The public Docker image is intentionally thin:

- It contains the MERIT-ML UI/runtime only.
- It does **not** contain `merit-cache-workbench-full-v7/` or any local Metabolomics Workbench dump.
- It reads hosted MERIT-derived assessment artifacts from the configured MERIT/R2 artifact root at runtime.
- Source-specific tabular exports are generated on demand from Metabolomics Workbench REST, not from a bundled mirror.

Because the image is thin, users need an internet connection for normal study lookup and for MERIT-derived tabular export generation.

## Run The Published Image

```bash
docker pull banerjee28/merit-ml:v7
docker run -d --name merit-ml -p 8780:8773 banerjee28/merit-ml:v7
```

Open:

```text
http://localhost:8780
```

Port mapping format is `HOST_PORT:CONTAINER_PORT`. MERIT-ML listens on port `8773` inside the container, so the host-side port can be changed if needed:

```bash
docker run -d --name merit-ml -p 8773:8773 banerjee28/merit-ml:v7
```

On Linux, prefix Docker commands with `sudo` if your user is not in the Docker group.

## Stop Or Restart

```bash
docker stop merit-ml
docker rm merit-ml
```

If the name is already in use:

```bash
docker ps -a | grep merit-ml
docker rm -f merit-ml
```

Then start it again with the `docker run` command above.

## What The Image Contains

The image includes:

- the Flask/Gunicorn MERIT-ML UI runtime;
- static UI assets needed to render the local browser app;
- the configured default pointer to the hosted MERIT assessment artifact root.

The image excludes:

- `merit-cache-workbench-full-v7/`;
- Metabolomics Workbench raw/source dump folders;
- local manuscript outputs, figures, scratch folders, and private deployment credentials;
- generated MERIT-derived export ZIPs.

## Runtime Data Access

Study reports are loaded from hosted MERIT-derived assessment artifacts using:

```text
MERIT_UI_PRECOMPUTED_ROOT=https://pub-acf151eb41e04ee795a86a8049d54039.r2.dev/merit-cache/releases/v7.2026-04-30-190939.metabatch-annotation-compatibility/
```

You can override this at runtime if you maintain your own compatible assessment-artifact endpoint:

```bash
docker run -d --name merit-ml -p 8780:8773 \
  -e MERIT_UI_PRECOMPUTED_ROOT=https://example.org/path/to/merit-artifacts/ \
  banerjee28/merit-ml:v7
```

The **Generate MERIT Export ZIP** workflow fetches source-specific tables live from Metabolomics Workbench REST at download time, aligns labels/settings inside the browser session, and writes a MERIT-derived assessment export. MERIT does not modify Metabolomics Workbench records and does not maintain a persistent server-side mirror of generated exports.

## Build Locally

Run from the `ML-ready` repository root:

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
```

Or with compose:

```bash
docker compose -f docker-compose.merit.yml build
```

Run the local build:

```bash
docker run --rm -p 8780:8773 merit-ml:v7-local
```

## Publish To DockerHub

Log in with a DockerHub access token, not a password written into a file:

```bash
docker login -u <dockerhub-username>
```

Build, tag, and push:

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t banerjee28/merit-ml:v7 .
docker tag banerjee28/merit-ml:v7 banerjee28/merit-ml:latest

docker push banerjee28/merit-ml:v7
docker push banerjee28/merit-ml:latest
```

## Publish To GitHub Container Registry

If the GHCR package is enabled for the repository:

```bash
echo "$GITHUB_PAT" | docker login ghcr.io -u <github-username> --password-stdin

docker tag banerjee28/merit-ml:v7 ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker tag banerjee28/merit-ml:v7 ghcr.io/biosystemengineeringlab-iitb/merit-ml:latest

docker push ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker push ghcr.io/biosystemengineeringlab-iitb/merit-ml:latest
```

## Verification Checklist

After building the image, confirm that no cache is present:

```bash
docker run --rm banerjee28/merit-ml:v7 sh -lc \
  'find /opt/merit -maxdepth 4 -iname "*merit-cache*" -o -iname "*mw-dump*"'
```

The command should not print a bundled Workbench cache directory.

Then smoke-test the UI:

```bash
docker rm -f merit-ml-test 2>/dev/null || true
docker run -d --name merit-ml-test -p 8782:8773 banerjee28/merit-ml:v7
curl -fsSL http://127.0.0.1:8782/healthz
docker rm -f merit-ml-test
```
