# MERIT-ML v2 · live Docker application

**Release: 8 November 2026 — `v2-live-20261108-r12`.** MERIT-ML assesses public Metabolomics Workbench studies in your browser, using your own computer for the calculations. This Docker distribution contains Python, its dependencies, the interface and the metric implementations. Install Docker once; no separate Python installation is needed.

**Guided walkthroughs:** The search page and the first generated report each have a Next/Back tour. Skip or close at any time, or choose “Don’t show tours automatically”. Completion and opt-out are saved in browser storage for that site; subsequent studies and report updates do not restart the tours. “Take a tour” replays them. These preferences are separate for different browsers/origins (including a newly allocated Docker port), and clearing site data resets them. The walkthrough changes no scientific input or result.

**Also included:** the supplied MERIT-ML logo now appears on the landing page and report header, with transparent light/dark variants that follow the theme toggle. Deposited study disease/condition metadata is retained in the report header and study index (for example, ST000010 reports Cancer). Bulk assessment provides an underlined link to each completed study’s full interactive report, opening in a new tab with its original settings. These links survive a server restart while the associated files remain in the study volume; deleting those files expires the link. No additional Workbench fetch is needed to open a completed report.

Enter a study ID such as `ST000001`. The application retrieves the study's inputs from Workbench and calculates its metrics locally. It queries **`/datatable/`**, mwTab text/JSON and `untarg_data` independently. It does not read the frozen cohort's v8/v9 study results or require the historical deposit. The fixed R1 alert reference, curated control rules and versioned global chemical reference assets are included; these are declared reference inputs, not downloaded study results. Context evidence follows the same versioned reference policy as the browser and 8791: original-cohort reviewed design labels and Pan-ReDU evidence are retrieved on demand from the public reference release and checked against its SHA-256 index. New studies outside that reference use the live programmatic design rules. These contextual reference inputs are identified explicitly; no quantitative study matrix or metric result is bundled.

## Start

**Registry status:** published publicly on GHCR for Linux AMD64 and ARM64. No GitHub login is required to download the image.

With Docker running, use:

```sh
docker run -d --name merit-v2 --init -p 127.0.0.1::8791 -v merit-v2-studies:/app/runs ghcr.io/biosystemengineeringlab-iitb/merit-ml:v2-live-20261108-r12
docker port merit-v2 8791/tcp
```

**Linux permissions:** If Docker reports “permission denied”, add `sudo` before each Docker command:

```sh
sudo docker run -d --name merit-v2 --init -p 127.0.0.1::8791 -v merit-v2-studies:/app/runs ghcr.io/biosystemengineeringlab-iitb/merit-ml:v2-live-20261108-r12
sudo docker port merit-v2 8791/tcp
```

Use the same prefix for `sudo docker compose`, `sudo docker stop` and `sudo docker start`. Windows and macOS Docker Desktop users can use the commands without `sudo`.

Docker selects an available **localhost port**, so an application already using 8791 will not block startup. The second command prints the assigned address, for example `127.0.0.1:49153`; open **http://127.0.0.1:49153** using the port actually printed. Port 8791 remains internal to the container. Check the address again after restarting or recreating the container, because Docker may assign a different port. The [Docker publication workflow](https://github.com/BiosystemEngineeringLab-IITB/merit-ml/actions/workflows/merit-v2-docker.yml) passed; see [release validation](docs/merit-v2-validation.md) for the test evidence. Docker selects the matching Linux image for Intel/AMD or ARM systems. Windows and macOS use Docker's Linux container environment.

To build from source instead, download this repository or the source bundle, open a terminal in its top-level folder and run:

```sh
docker compose -f compose.merit-v2.yml up -d --build
docker compose -f compose.merit-v2.yml port merit 8791
```

The Compose command also chooses an available localhost port; open the address printed by its second command with `http://` in front.

If you need a fixed port instead, replace `-p 127.0.0.1::8791` with, for example, `-p 127.0.0.1:8800:8791`.

If you have an AMD64 image archive exported from this release, no dependency build is needed:

```sh
docker load -i merit-v2-image.tar.gz
docker run -d --name merit-v2 --init -p 127.0.0.1::8791 -v merit-v2-studies:/app/runs merit-ml:v2-live-20261108-r12
docker port merit-v2 8791/tcp
```

That archive is for Intel/AMD Linux containers, including Docker on Intel/AMD Windows or macOS. The source build selects your platform automatically; the registry workflow builds both AMD64 and ARM64.

For the `docker run` installation, stop with `docker stop merit-v2` and restart with `docker start merit-v2`. For Compose, stop with `docker compose -f compose.merit-v2.yml down`. The named volume retains your downloaded inputs and calculations. `docker compose ... down -v` also deletes that volume. Completed web jobs normally expire after 24 hours; add `--retain` to the container command if you need them kept. Export reports you want to preserve.

## What the profile means

The application reports matrix properties, analytical alerts against fixed R1 and sample-count support under your selected classes and folds. It does not combine them into a universal readiness score or certify predictive performance. Each analysis × data endpoint is assessed separately. You can change classes, folds, allowed check settings, sample eligibility, labels and subject information in the interface.

This native v2 build retains the arrays needed for exact recalculation when resources permit, rather than imposing the browser version's matrix-size ceiling. Storage and processing time therefore grow with the study.

## A large matrix must not discard the other results

Every initial check and interactive calculation runs behind a **matrix-specific process boundary**. Successful results are retained. A worker that runs out of its memory budget, exceeds its time limit or raises an error is reported as **not assessed**, with its reason. Its result is not replaced with zero or a passing score. Other matrices and independent checks continue. Shared metadata or input-fetch failures are identified separately. Exceptionally large report/export lists are checked against available memory before loading; the underlying calculation is retained if only its report cannot fit.

The server reserves memory headroom using the available host/container memory. There is no fixed cell-count exclusion. Resources are finite: an OS-level failure or forced container shutdown can still interrupt the service. Checkpoints preserve calculations already saved; they do not make unlimited matrices executable on insufficient hardware.

Optional controls for `docker run` (place before the image name):

| Setting | Default | Effect |
|---|---:|---|
| `-e MERIT_WORKER_MEMORY_MB=4096` | automatic: 65% of available memory | Per-worker memory budget in MiB; 0 selects automatic |
| `-e MERIT_CHECK_TIMEOUT_SECONDS=3600` | 1800 | Maximum duration per calculation/check; 0 disables this timeout |
| `-e MERIT_RENDER_TIMEOUT_SECONDS=7200` | 3600 | Maximum wait for a rendered report/export |
| `--memory=8g` | Docker's allocation | Optional overall container limit; leave headroom for the server |

Execution details are saved to each job's `matrix_execution.json`, with logs, elapsed time, measured peak RSS and the budget. A running service also exposes `/api/v2/execution/<job-id>`. Downloads, working arrays and reports are stored only in the local Docker volume. Size depends on the studies assessed; the application does not download the entire cohort at startup. MW and external evidence services require internet access.

This is a personal local application. The command binds its port to localhost. Do not expose it publicly without a separate authenticated service deployment and capacity controls.

## Update and reproduce a release

The source capture is [docker/merit-v2/runtime](docker/merit-v2/runtime); [runtime_capture.json](docker/merit-v2/runtime/runtime_capture.json) records its provenance. Native supervision and partial-fetch handling use the same captured runtime as 8791. Browser-only memory limits are not applied to this native application. [Build/update instructions](docker/merit-v2/README.md) explain synchronization, rebuilding, validation and packaging. The original cache-based v7 distribution is documented in [the archived README](docs/legacy-v7-readme.md); this release does not replace its old image tag.

No credentials, private keys, deployment configuration or cohort caches belong in the image or source bundle. Commits use the maintainer's existing Git identity, without agent co-author trailers. Existing repository history is preserved.

The start page provides study-ID search, recently released Workbench studies, About, Publication and GitHub links, and a light/dark theme. Bulk assessment on the landing page accepts typed or pasted study IDs, removes duplicates, and checks availability before running. Available IDs are green, invalid/unavailable IDs red, confirmed embargoes amber and unverified IDs gray. The browser edition displays a disabled bulk area linking to Docker. `/workspace` redirects to this landing page. Reports open at the assessment heading; metric definitions and calculations are unchanged.
