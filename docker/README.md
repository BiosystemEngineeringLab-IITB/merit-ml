# MERIT Local Docker Package

This package builds a local MERIT web UI image that includes the current precomputed Workbench assessment cache. Users can run the UI in their own browser without depending on the hosted MERIT site.

The readiness reports are served from the bundled cache. The **Download Tabular Data** feature still fetches tabular matrices live from Metabolomics Workbench REST endpoints, so that feature requires an internet connection.

## Recommended: Use The Published Image

Most users should pull and run the published image:

```bash
docker pull ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker run -d --name merit-ml -p 8780:8773 ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

Then open:

```text
http://localhost:8780
```

MERIT listens on `8773` inside the container. The host-side port can be changed, for example `-p 8773:8773` or `-p 8780:8773`.

Stop and remove the container with:

```bash
docker stop merit-ml
docker rm merit-ml
```

## Build Locally

Run from the repository root with the MERIT cache directory present:

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

## Run A Local Build

```bash
docker compose -f docker-compose.merit.yml up
```

Then open:

```text
http://localhost:8773
```

To run without compose:

```bash
docker run --rm -p 8780:8773 merit-ml:v7-local
```

Then open:

```text
http://localhost:8780
```

## Publish Through GitHub Container Registry

Do not commit the multi-GB cache as normal Git files. Publish the built image through GitHub Container Registry instead.

Build once, then publish the image:

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
docker tag merit-ml:v7-local ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker push ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

Users can then run:

```bash
docker pull ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker run -d --name merit-ml -p 8780:8773 ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

If `docker pull` returns `denied`, the package is private or the user does not have GitHub Package access. Anonymous pulls require the package visibility to be **Public**.

## Alternative: GitHub Release Asset

Export the image as a compressed artifact:

```bash
docker save merit-ml:v7-local | gzip > merit-ml-v7-local-docker-image.tar.gz
```

Attach the `.tar.gz` file to a GitHub Release. Users can load it with:

```bash
docker load < merit-ml-v7-local-docker-image.tar.gz
docker run --rm -p 8780:8773 merit-ml:v7-local
```

## Notes For Users

- The container serves cached MERIT readiness reports locally.
- The browser opens on the user's own system at `http://localhost:8780` when using the recommended run command.
- To access MERIT from another computer on the same network, open `http://<HOST-IP>:8780` and ensure the host firewall allows the selected port.
- The **Download Tabular Data** tab performs live Workbench REST API calls and needs internet access.
- MERIT does not write new cache files inside the container during normal UI use.
