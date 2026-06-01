# MERIT

MERIT (MachinE learning ReadIness for Tabular metabolomics data) provides a web interface for assessing machine-learning readiness of tabular metabolomics datasets from Metabolomics Workbench.

Hosted version:

```text
https://merit-ml.in
```

This repository provides source code and Docker instructions for running MERIT locally.

## Quick Start: Run With Docker

Install Docker, then run:

```bash
docker pull ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
docker run --rm -p 8773:8773 ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

Open:

```text
http://localhost:8773
```

If port `8773` is already in use:

```bash
docker run --rm -p 8780:8773 ghcr.io/biosystemengineeringlab-iitb/merit-ml:v7
```

Then open:

```text
http://localhost:8780
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

Most users should use the prebuilt image above. Building the image from source requires the MERIT v7 cache directory to be present at repository root as:

```text
merit-cache-workbench-full-v7/
```

Then run:

```bash
docker build -f docker/merit-ui-v2.Dockerfile -t merit-ml:v7-local .
docker run --rm -p 8773:8773 merit-ml:v7-local
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
