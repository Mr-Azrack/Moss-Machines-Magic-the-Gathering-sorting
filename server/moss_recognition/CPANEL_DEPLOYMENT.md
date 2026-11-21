# cPanel / Passenger deployment checklist

This is a **standalone** service. Do not copy files into the sorter PC or replace any existing website application.

1. Confirm the intended domain points to the hosting account and HTTPS is active. A newly created subdomain may take time to resolve publicly.
2. In the chosen cPanel account, select **Setup Python App** (CloudLinux) if available. Otherwise use the host's supported **Application Manager / Passenger** interface. Create a new app whose **Application URL** is the desired subdomain at `/` and whose **Application root** is a dedicated directory **outside `public_html`**.
3. Select a supported Python version (3.11 or 3.12 preferred for broad compatibility), set **Startup file** `passenger_wsgi.py`, and **Entry point** `application`. These names match the shipped source files.
4. Copy the complete contents of `server/moss_recognition/` into that application root. The `passenger_wsgi.py`, `server.py`, and `requirements.txt` files must be at the *root of the Python app*, not in a nested ZIP folder.
5. Install Pillow into the **app's** Python environment with `pip install -r requirements.txt` using the command supplied by Setup Python App. No heavyweight model or separate database daemon is required for v0.1.
6. Add these environment variables in the application's configuration (never commit values, send them through chat, or place them under web-accessible folders):
   - `MOSS_API_KEY`: unique random secret, at least 16 characters (32+ recommended)
   - `MOSS_ADMIN_PASSWORD`: a different long random secret
   - `MOSS_SESSION_SECRET`: a third independent random secret (32+ characters recommended)
   - `MOSS_DATA_DIR`: **absolute** private directory outside `public_html`, under the correct cPanel user's home directory
   - `MOSS_COOKIE_SECURE=1`
7. Restart the app, visit `https://YOUR-DOMAIN/health`, and expect `{"status":"ok","service":"moss-recognition","version":"0.1.0"}`. Open `https://YOUR-DOMAIN/admin` to use the review dashboard.
8. Confirm `MOSS_DATA_DIR` permissions allow the app to create `moss_recognition.db` and `scan_images/`. These must remain private, and both require backups.
9. If a request fails, inspect the **Passenger application log** and Python dependencies before altering any live production site. Do not disable HTTPS, secure cookies, or authentication to troubleshoot.
10. Keep the sorter integration disconnected until the server has been benchmarked in **advisory/shadow mode**, with positive operator verification of card identities.

## Self-checks

- Domain DNS resolves; valid HTTPS certificate is present.
- `/health` responds with the expected JSON.
- `/admin` requires login; after login, there are no training examples until an operator explicitly verifies a real scan.
- No image or SQLite files are exposed under the domain's public document root.
- Existing sites, sorter Python files, and Arduino firmware remain unchanged.

More detail: [README.md](README.md).

## Catalog matcher pilot

The existing Passenger app exposes authenticated `POST /api/v1/recognize`.
Send `{"game":"magic","algorithm":"moss-rgb-phash-256","rgb_phash":[r,g,b]}`
with the three 64-digit hex values from the MOSS RGB pHash scanner and the existing
`X-MOSS-API-Key` header. Add a lowercase `set` only when the physical set is
known. Responses include the candidate and runner-up distances, catalog scope,
version, and `advisory_only: true`. HTTP 503 means there is no usable catalog.
The endpoint neither stores a scan nor makes upstream requests during matching.

Run the catalog refresh with the app's virtualenv Python. The app and cron must
use the same private data directory.

```sh
/home/thegamestationlw/virtualenv/moss_recognition/3.12/bin/python \
  /home/thegamestationlw/moss_recognition/refresh_catalog.py \
  --data-dir /home/thegamestationlw/moss_private_data --sets fdn:23556,dft
```

Install that command in cPanel Cron Jobs once a day. The pilot searches two
sets. It uses a few Scryfall search pages per set, supplements Foundations with
the TCGTracking set feed, and calculates fingerprints only for image URLs absent
from the last snapshot. The daily refresh uses an exclusive lock and publishes
via atomic replacement. A failed refresh leaves the last usable index available.
Use Scryfall's compressed Default Cards bulk manifest for a future full-game
rollout. The pilot is advisory because a missing out-of-set reprint can change
the match decision.

## Full paper Magic index

Use `refresh_full_mtg.py --data-dir /home/thegamestationlw/moss_private_data
--image-budget 1500` in the existing daily cron. It reads Scryfall's Default Cards
bulk manifest, keeps paper records, and joins the Magic MOSS reference hashes by
TCGplayer product ID. References and catalog snapshots live only in the private
data directory. A repeat run saves an unchanged snapshot and uses available
fingerprints before fetching missing images. Coverage reports how many catalog
records still need a reference. The source uses Scryfall's default language
selection. Its separate All Cards multilingual export is outside this job.

`POST /api/v1/recognize` accepts an optional `evidence` object with
`set_abbreviation`, `symbol_set`, `collector_number`, and `foil_mark`
(`star`, `dot`, or `unknown`). Supply only confident visual observations.
A symbol/set disagreement returns an explicit conflict. A star selects a
premium finish only when the indexed printing has exactly one premium finish.
Results remain advisory. The endpoint verifies text and marks that the vision
client sends. It does not extract them from RGB fingerprints.

For the camera's evidence image, `camera_evidence.evidence_crop` accepts
the original frame and detected corners in TL, TR, BR, BL order. It leaves
2 mm of real image outside each card edge and rejects clipped frames. Use the
current tight crop for fingerprint generation. No live capture pathway imports
this helper yet.
