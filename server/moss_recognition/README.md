# MOSS Recognition Center — v0.1

Independent, low-resource **card-recognition review and verified-example service** for MOSS. Designed for a separate cPanel Python application and SQLite storage. This is a real, working first stage of the central recognition system, **not** yet a trained neural-network AI model.

**Do not replace the existing MOSS sorter recognition engine or install this on the sorter PC yet.** The Arduino and sorter Python files are unchanged. Every response is `advisory_only: true`; a remote match must never bypass the sorter's own identification safety checks or command the Arduino.

## What is implemented

- A WSGI web app (Python standard library + **Pillow** only); no Flask, PyTorch or GPU required.
- Authenticated `POST /api/v1/scans`: accept a real JPEG/PNG scan and optional on-device candidates; normalize/store the image in a **private** directory and record the scan as `pending`.
- Protected human review dashboard at `/admin`: inspect saved images, select exact candidate or enter a correction, explicitly verify the physical printing, and approve/reject.
- Only human-verified identities populate a reference gallery (64-bit dHash). Subsequent `POST /api/v1/match` requests return **advisory** visually similar examples; this is instance-based learning rather than neural-model retraining.
- Authenticated `GET /api/v1/metrics` and `GET /api/v1/scans/<scan_id>` for monitoring; public `GET /health`.
- Separate API token and admin password, rate-limited admin login, signed session cookies, CSRF protection on review forms, HTML escaping, image size limits, private image routes, SQLite audit log, and reject-on-uncertainty design.
- Tests run without a live camera, network, or Arduino.

### Limitations by design

- The server does **not** know the entire card catalog yet. Candidate IDs/metadata originate with the sorter, and an operator must check them. Manual corrections require product ID, card name, set, and collector number. We will add authoritative catalog checks before allowing a server decision to participate in sorting.
- dHash is deliberately lightweight and can mistake same-art printings for one another. The returned `dhash_distance` is **not** a calibrated confidence probability. Never auto-sort based on the remote suggestion alone.
- `MAX_EXAMPLES_SEARCHED=20000` bounds full-gallery comparisons on shared hosting; once the gallery grows beyond it, lookup explicitly reports `gallery_limit_exceeded` instead of silently returning incomplete matches. Future revisions will add an indexed search.
- Host limits for background workers, model weights, CPU, memory, and automated inference have **not** been verified. A larger AI model should be hosted elsewhere if cPanel limits prevent it.
- No full device synchronization, automated installer, internet fallback, account-specific roles, model training, purge/retention scheduler, or sorter-side adapter has been deployed. These remain tracked in `ROADMAP.md`.

## Server deployment on cPanel

1. In WHM create a **dedicated cPanel account** for MOSS, using an owned domain or a permitted subdomain. Don't put this app in an existing production website's root; shared hosting resource quotas still apply.
2. In that account create a Python web app using **Setup Python App** or the supported Python/WSGI feature. Choose Python 3.10 or later (3.11/3.12 are reasonable), **Application Root** outside `public_html`, and an HTTPS URL like `https://moss.example.com/`.
3. Upload the **complete contents of this folder** to that application root, e.g. `/home/mossuser/moss_recognition/`. The WSGI startup file is `passenger_wsgi.py`, exposing callable `application`.
4. Install dependencies within the app's provided virtualenv: `pip install -r requirements.txt`. It requires only Pillow.
5. Add these *actual* environment variables using the cPanel app's environment-setting interface (use different random secrets; never paste them into GitHub, chat, or the `.env.example` checked into source):

   - `MOSS_API_KEY`: minimum 16 characters, recommended `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
   - `MOSS_ADMIN_PASSWORD`: an independent long password (not the cPanel password).
   - `MOSS_SESSION_SECRET`: different generated random 32+ character secret.
   - `MOSS_DATA_DIR=/home/mossuser/moss_private_data` (**not inside `public_html`**).
   - `MOSS_COOKIE_SECURE=1` (default; HTTPS required for admin login).

6. Restart the Python app. Test `https://moss.example.com/health` in a browser; expected: `{"status":"ok","service":"moss-recognition","version":"0.1.0"}`. Sign in at `/admin` using the independent admin password.
7. Verify the provider's Python memory, CPU, process duration, upload size and storage limits. Begin with a **small number of scans**. Do not link it to active sorting until shadow-mode accuracy and latency tests pass.

**Important:** On many cPanel/CloudLinux installations the **Startup File** is `passenger_wsgi.py` with **Application Entry Point** `application`. Some hosts manage Passenger automatically. Confirm the exact hosting interface rather than assuming any shell command starts a persistent process. Local development only: `python server.py` uses the standard-library HTTP server on `127.0.0.1:8080`; it is NOT a public production server.

## API contract

All API requests except `/health` require header `X-MOSS-API-Key: <secret>` and HTTPS in production. Clients must send `Content-Type: application/json` and `Content-Length`.

### Create a pending scan

`POST /api/v1/scans`

```json
{
  "device_id": "MOSS-01",
  "image_base64": "<BASE64_JPEG_BYTES>",
  "candidates": [
    {"product_id":"123456","name":"Example Card","set":"FDN","collector_number":"42","distance":5.2}
  ]
}
```

A successful `201` response contains:

```json
{
  "version":"0.1.0",
  "advisory_only":true,
  "suggestions":[],
  "warning":null,
  "message":"Do not use server suggestions alone to command sorter motion.",
  "scan_id":"<generated id>",
  "state":"pending"
}
```

New images are **not** used for future matching until physically verified on the review dashboard.

### Match against approved image examples

`POST /api/v1/match` accepts the same JSON and responds `200` without storing a new scan. A suggestion has `product_id`, `name`, `set`, `collector_number`, and `dhash_distance` (0 is the closest possible perceptual hash). Results are **advisory** and do not certify exact printing identity.

### Read-only status and counts

- `GET /api/v1/metrics`
- `GET /api/v1/scans/<scan_id>`
- `GET /health` (public, no secret disclosed)

No movement-control or remotely authorized bin-routing endpoints exist.

## Run tests

From the folder containing `server.py`:

```sh
python -m unittest discover -s tests -v
```

Tests use a temporary SQLite database and synthetic card images; they do not call the network or hardware. They cover API authentication, image validation, pending-only ingest, approval/rejection rules, CSRF, manual correction, HTML escaping, sign-in throttling and advisory-only retrieval.

## Operational rules

- Keep `MOSS_DATA_DIR` private; back up **both** `moss_recognition.db` and `scan_images/` together. Don't copy production scans into public GitHub.
- Prefer a dedicated service/API token for each sorter in a later revision; v0.1 uses one API token for the pilot.
- Set up a retention and storage-capacity plan before sending thousands of scans; a 200 KB JPEG per card is ~200 MB per 1,000 scans (illustrative only).
- Repeated scans and manual verification are separate from automatic neural-network retraining. Future model updates will be versioned and evaluated against a fixed test set.
- Continue using the existing **local** fail-closed identity gate, reject bin, and existing Arduino command protocol.

## Project continuity

`PROJECT_STATE.md`, `DECISIONS.md`, `ROADMAP.md`, and `SESSION_HANDOFF.md` record state so progress can continue across sessions.
