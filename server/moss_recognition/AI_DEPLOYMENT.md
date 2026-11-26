# MOSS server-side AI recognition (staged, opt-in)

## What is implemented
- `POST /api/v1/recognize-image`: accepts a cropped Magic card JPEG/PNG over existing HTTPS, authorized by **lookup-only** or existing full API credential.
- The API computes RGB pHash **on the server**, consults a **private loopback OCR worker**, and checks the verified set/collector evidence against the full MOSS printing index.
- `ai_worker.py` runs PaddleOCR for the printed footer and the repository's trained ResNet18 CNN for the set symbol on a Linux CPU worker. Set-symbol evidence helps narrow set/collector matches; it never identifies a card by itself.
- An unreadable footer, cropped-off text, missing worker, transport error, incomplete catalog evidence, or ambiguous printing is **not** allowed to become an automated card ID.
- `export_verified_training.py` builds a private manifest from **only operator-approved examples** for later fine-tuning and benchmark sets. The existing operator review dashboard remains the truth source.
- `diagnostics/server_ai_lookup.py` enables a new camera-only test path, reusing the existing encrypted sorter lookup credential and tray preview. **No physical motor commands** have been changed.

## Readiness / NOT YET DONE
This code has been developed and unit-tested; it is **not proof of production AI readiness**. It requires a live worker, installed OCR model weights, resource benchmark on the actual hosting plan, and real camera captures.
**No automatic live sorting is enabled** by this branch.

### Private CPU worker installation (Python 3.11 or 3.12)
Use a dedicated Linux Python environment; do not install PaddleOCR in the existing lightweight cPanel WSGI interpreter unless resources are verified.

1. Provision a Linux host / private process with enough RAM and persistent disk for PaddleOCR + CPU inference. Test memory and p95 latency before connecting the sorter.
2. Install Python 3.11/3.12, official CPU-only PyTorch/torchvision wheels, and a CPU-compatible PaddlePaddle runtime using the official installation instructions. Install `paddleocr`, `numpy`, and `Pillow`. The existing WSGI service still needs only Pillow.
3. Configure **the same new random 32+ character** `MOSS_AI_WORKER_TOKEN` in the private worker and WSGI processes. Store it in private env/secrets, **never** in Git, public_html or the sorter. This token is independent of the existing MOSS lookup key.
4. Start: `python ai_worker.py`. It will initialize the real PaddleOCR models and bind **127.0.0.1:8765 only**. The local GET `/health` returns ready after model initialization.
5. If the worker runs on a different machine, set up an authenticated private tunnel such that **127.0.0.1:8765 on the WSGI host** securely reaches the worker. Never publish the worker HTTP port externally.
6. Deploy `ai_image_api.py` and the small `server.py` route addition to the existing Recognition Center. Keep the old `/api/v1/recognize` endpoint intact.
7. Start with sample scans and camera-only test before any possible motor integration.

Official PaddleOCR docs: https://www.paddleocr.ai/latest/en/quick_start.html

### Sorter PC opt-in (no motors)
Place `server_ai_lookup.py`, updated `hosted_camera_live.py`, `START_AI_CAMERA_TEST.ps1` and `START_AI_CAMERA_TEST.cmd` alongside the existing `fixed_height_preview.py` and diagnostic client files. The local Windows DPAPI-protected lookup key already provisioned to the sorter is reused.

Close every other app using the UC60 camera. Run `START_AI_CAMERA_TEST.cmd`, then test a known nonfoil and a foil with the physical camera, comparing **printing, collector number, finish, and proposed tray**. The 2mm *outside* margin remains optional; no printed card pixels may be sacrificed. The test does not interact with the Arduino or motors.

### Model limitations and acceptance gates
- PaddleOCR gives a *learned text recognizer*, not a fine-tuned Magic artwork model. The art branch remains pHash. A trained set-symbol CNN now runs alongside OCR. Its checkpoint covers 74 trained symbol classes; the Scryfall sync stores all set icons and names privately, while the model's symbol prediction remains supporting evidence only.
- Modern cards with printed set abbreviation and number are first candidates. Many older printings lack these footers and are intentionally sent to review until a symbol/image alternative is proven.
- Finish recognition via printed foil star/dot is conditional and may be missing; do not force a nonfoil/foil classification without evidence.
- Check **false-accept rate per printing/finish**, recall, coverage by era, CPU/RAM usage, and p95 latency using physical card captures. Never assert an ML confidence score merely because a pHash candidate matched.
- Only after a real-world review should the automatic sorter’s decision gate be changed. Outage must **pause feeding**, uncertain cards must go to review, and the original application must remain restorable.

## Training data
`python export_verified_training.py --data-dir /private/moss_private_data --output-dir /private/moss_private_data/training`

It exports approved-image paths and exact printing labels, split by product ID. This is a dataset manifest only: to train OCR detection or recognition, crop and label specific text regions; to train an artwork model, add an embedding encoder and its training/evaluation job. Never auto-train on an unapproved match.


## Prepared without the sorter (November 20, 2026)

The feature branch now also provides:
- `install_ai_worker.sh`: isolated Python 3.11/3.12 CPU-worker installer for a *dedicated Linux account/host*, including CPU-only PyTorch/torchvision, with no public listener and no changes to the production cPanel WSGI app. Requires the official PaddlePaddle CPU package index.
- `requirements-ai-worker.txt`: pinned initial PaddleOCR test environment. The production WSGI app still only needs `requirements.txt` (Pillow).
- `ai_preflight.py`: read-only readiness inventory of Python, RAM availability, catalog, private storage, worker reachability, and whether credentials are configured. No secrets are printed.
- `benchmark_ai_worker.py`: actual loopback worker latency measurement; real-card correctness must still be evaluated separately.
- `set_symbol_model.py`: CPU wrapper for the trained CNN checkpoint.\n- `refresh_scryfall_sets.py`: downloads Scryfall set names, alternate codes, and SVG set icons into private VPS storage; the full catalog refresh runs it automatically.\n- `build_ai_bundle.py`: reproducible allowlist-only source bundle that excludes credentials, catalog data, and camera images.
- CI publishes the `moss-ai-staging-source` artifact *after passing the existing safety tests*. Download it from the completed MOSS Recognition Server GitHub Actions run.
- The AI image endpoint returns up to three image-similarity suggestions for cards missing a reliable modern footer, **but never upgrades such suggestions to an auto-sortable candidate**.

### Before starting a live AI worker
Verify host account resource limits with:
```bash
python ai_preflight.py --data-dir "$MOSS_DATA_DIR"
```
A worker cannot be marked ready until **all** of the catalog, credential, and OCR-worker checks succeed:
```bash
python ai_preflight.py --data-dir "$MOSS_DATA_DIR" --strict
```
For a dedicated Linux worker, first copy the staged bundle to the worker account and run `bash install_ai_worker.sh` as an **unprivileged** user, then supply `MOSS_AI_WORKER_TOKEN` through the private service environment, run `ai_worker.py` bound to loopback, and measure latency with `benchmark_ai_worker.py`. The worker must not share cPanel resources unless measured limits permit it.

### Deployment blockers that do NOT involve the sorter
- Hosting control-panel/terminal access is required to inspect the actual resource limits and install the worker. Browser tabs alone do not give an authorized shell connection.
- The model must be initialized and benchmarked with real PaddleOCR weights on the target machine. **Mock-model HTTP tests do not satisfy that gate.**
- The new WSGI route must be deployed independently, health-checked, and left advisory-only before enabling the sorter-side AI camera client.

### What remains tied to the physical sorter
- Fresh cards with reliable known labels from a range of sets/eras and finishes.
- End-to-end recognition and camera-cycle latency, especially whether card-footers stay in the fixed-height frame.
- A controlled dry run with no bin movement, then explicit physical motion validation.
