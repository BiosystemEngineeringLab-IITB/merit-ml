# MERIT

MERIT (MachinE learning ReadIness for Tabular metabolomics data) provides a web interface for assessing machine-learning readiness of tabular metabolomics datasets from Metabolomics Workbench.

Hosted version:

```text
https://merit-ml.in
```

This repository provides source code and Docker instructions for running MERIT locally.

## Quick Start: Run With Docker

Install Docker, then pull the MERIT image from Docker Hub:

```bash
docker pull banerjee28/merit-ml:v7
```

Run the local web app:

```bash
docker run -d --name merit-ml -p 8780:8773 banerjee28/merit-ml:v7
```

Open:

```text
http://localhost:8780
```

Port mapping format is `HOST_PORT:CONTAINER_PORT`. MERIT listens on port `8773` inside the container, so you can change only the host-side port if needed:

```bash
docker run -d --name merit-ml -p 8773:8773 banerjee28/merit-ml:v7
```

On Linux, prefix Docker commands with `sudo` if your user is not in the Docker group.

## Stop Or Restart MERIT

Stop the running container:

```bash
docker stop merit-ml
```

Remove the stopped container before starting a new one with the same name:

```bash
docker rm merit-ml
```

If Docker reports that the name is already in use, run:

```bash
docker ps -a | grep merit-ml
docker stop merit-ml
docker rm merit-ml
```

Then start it again with the `docker run` command above.

## Access From Another Computer On The Same Network

If MERIT is running on one computer and you want to open it from another computer on the same network, use the host computer's IP address:

```text
http://<HOST-IP>:8780
```

For example, if the host IP is `192.168.1.25`, open:

```text
http://192.168.1.25:8780
```

Your firewall must allow inbound traffic on the selected host port.

## GitHub Container Registry Mirror

A GitHub Container Registry image may also be available:

```bash
docker pull ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

If this returns `denied`, use the Docker Hub image above or log in with a GitHub account that has package access:

```bash
docker login ghcr.io
docker pull ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

## What The Docker Image Contains

The Docker image includes:

- the MERIT UI runtime;
- the precomputed MERIT v7 assessment cache for Metabolomics Workbench studies;
- study browser, per-study readiness reports, source-level readiness bands, scoring-parameter controls, bulk MERIT analysis, and ML-ready data export controls.

Readiness reports are served locally from the bundled cache.

## Internet Requirement

The main readiness UI can run from the bundled cache. The **Download Tabular Data** feature fetches matrices live from official Metabolomics Workbench REST endpoints and therefore requires internet access.

Some chart assets may also load from public JavaScript CDNs depending on browser cache state.

## Build From Source

Most users should use the prebuilt image above. Building the image from source is intended for maintainers and requires the MERIT v7 cache directory to be present at repository root as:

```text
merit-cache-workbench-full-v7/
```

Then run:

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
docker run --rm -p 8780:8773 merit-ml:v7-local
```

## Repository Contents

```text
merit-ui-v2/              MERIT UI and Python runtime
docker/                   Dockerfile and Docker distribution notes
docker-compose.merit.yml  Local compose launcher
```

The large precomputed cache is intentionally not committed to Git history. It is distributed through the Docker image.

## Citation

If you use MERIT, please cite the associated manuscript and Metabolomics Workbench data source as described in the MERIT interface.
