# MERIT v2 Docker release — 2026-11-06

Build from the repository root:

```sh
docker build -f docker/merit-v2/Dockerfile -t merit-ml:v2-live-20261106-r9 .
```

The Dockerfile copies only this runtime and its pinned dependencies. `Dockerfile.dockerignore` excludes downloads, generated outputs and the old application. `relocate_bundle.py` verifies and rewrites captured absolute source paths inside the image.

For a later authorized update on the development machine:

```sh
python3 scripts/sync_merit_v2_runtime.py --source /path/to/ML-ready/online-tool
python3 docker/merit-v2/runtime/relocate_bundle.py
```

This explicitly whitelists source files, control rules, the fixed R1 and versioned global references and context checksum index. It never copies `runs`, cohort outputs, reviewed per-study design labels, Pan-ReDU snapshots or credentials. Per-study contextual references are retrieved on demand and verified against the same file index used by 8791/browser; they are identified as reference evidence, never freshly computed matrix metrics. Review the diff, rerun `scripts/test_merit_v2.py` with fresh-input fixtures, build the image and test it before committing. If a native bridge hook no longer matches the source, synchronization fails for manual review. Update the release date/version, workflow tag and validation report together for a new published release.

```sh
python3 scripts/package_merit_v2.py
```

The source archive goes to `release-artifacts/`, excluded from Git. It includes only the v2 Docker runtime, requirements, Dockerfile, Compose file, README, workflow, scripts, license and release documentation. It is small relative to a Docker image, but requires an internet-connected Docker build. For offline installation of the software, distribute `docker save merit-ml:v2-live-20261106-r9 | gzip > merit-v2-image.tar.gz`; Workbench retrieval still needs the internet. Do not distribute local downloaded study data as part of this source bundle.

The GitHub workflow builds AMD64 and ARM64 images and publishes the distinct `v2-live-20261106-r9` tag to GHCR. It never commits generated files. Package visibility may initially be private: an organisation maintainer may need to set the GHCR package to Public. See the root README for launch commands and resource controls.
