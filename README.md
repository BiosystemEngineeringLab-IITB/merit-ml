# MERIT-ML

MERIT-ML (Metabolomics Evaluation of Readiness and Interoperability of Tabular Data for Machine Learning) provides a web interface for assessing machine-learning readiness of tabular metabolomics datasets from Metabolomics Workbench.

Hosted version:

```text
https://merit-ml.in
```

This repository provides source code and Docker instructions for running MERIT-ML locally.

The public Docker distribution is a thin local UI container. It does **not** bundle the MERIT v7 cache, raw Metabolomics Workbench source records, or generated tabular exports. Study reports are loaded from hosted MERIT-derived assessment artifacts at runtime, and source-specific tabular exports are generated on demand from Metabolomics Workbench REST.

This release excludes Workbench records that are currently under embargo from the public MERIT-ML interface. Embargoed studies are not shown in search results, direct accession lookup, bulk analysis, or ML-ready data export until they are publicly available from Metabolomics Workbench.

## Quick Start: Run With Docker

Install Docker, then pull the MERIT-ML image from Docker Hub. The current thin-image `v7` and `latest` tags resolve to index digest `sha256:935bd325d5ff2cda44dcb6170dd1a6faad8ff17980176c34251e152aeb60d716`:

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

Port mapping format is `HOST_PORT:CONTAINER_PORT`. MERIT-ML listens on port `8773` inside the container, so you can change only the host-side port if needed:

```bash
docker run -d --name merit-ml -p 8773:8773 banerjee28/merit-ml:v7
```

On Linux, prefix Docker commands with `sudo` if your user is not in the Docker group.

## Stop Or Restart MERIT-ML

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

If MERIT-ML is running on one computer and you want to open it from another computer on the same network, use the host computer's IP address:

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

- the MERIT-ML UI runtime;
- static assets needed to render the browser interface;
- a default pointer to the hosted MERIT assessment artifact root;
- study browser, per-study readiness reports, source-level readiness bands, scoring-parameter controls, bulk MERIT analysis, and MERIT-derived export controls.

The Docker image does **not** include `merit-cache-workbench-full-v7/` or any local Metabolomics Workbench source-data dump.

## Internet Requirement

MERIT-ML Docker requires internet access for normal study lookup because reports are loaded from hosted MERIT assessment artifacts rather than a bundled cache. The **Generate MERIT Export ZIP** feature also fetches matrices live from official Metabolomics Workbench REST endpoints.

Some chart assets may also load from public JavaScript CDNs depending on browser cache state.

## Build From Source

Most users should use the prebuilt image above. Building the image from source does not require the MERIT v7 cache directory.

Then run:

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
docker run --rm -p 8780:8773 merit-ml:v7-local
```

## Repository Contents

```text
merit-ui-v2/              MERIT-ML UI and Python runtime
docker/                   Dockerfile and Docker distribution notes
docker-compose.merit.yml  Local compose launcher
```

The large precomputed cache is intentionally not committed to Git history and is not distributed through the Docker image. The public image reads hosted MERIT assessment artifacts at runtime.

## Citation

If you use MERIT-ML, please cite the associated manuscript and Metabolomics Workbench data source as described in the MERIT-ML interface. Also cite the preprint associated with this study: Shayantan Banerjee, Pramod P. Wangikar. MERIT-ML: A Machine-Learning-Readiness Framework for Tabular Public Metabolomics Data. ChemRxiv. 10 June 2026.
DOI: https://doi.org/10.26434/chemrxiv.15004429/v2
