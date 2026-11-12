# MOSS Recognition Center — Verified State

As of 2026-11-11, `server.py` provides a standalone WSGI recognition-review service. It has no runtime coupling to the MOSS sorter's Python scripts or the Arduino. Main features: authenticated scan ingest and persistence, SQLite audit metadata, protected review dashboard, manual correction of exact printing, verified-example gallery using dHash, advisory-only similarity API, status/metrics, CSRF/login protection and untrusted image validation.

## Verified locally

- `python -Werror -m unittest discover -s tests -v` — 13 tests passing at time of packaging.
- `python -m py_compile server.py passenger_wsgi.py` — syntax compilation successful.
- Tests use local temporary databases and synthetic images; no hardware or internet.

## Not yet verified

- Deployment in the user's WHM/cPanel account, Python environment version/wheels, quota and allowed WSGI process model.
- HTTPS certificate, app domain, and production secrets.
- Actual reference image accuracy for Magic cards and diverse TCGs.
- External sorter-to-server integration, throughput, timeout/fallback and operator review experience on real scans.
- Neural AI training/inference. The reference gallery is *human-approved example retrieval*, not neural training.

## Source/control baseline

- Sorter PC: `DESKTOP-9R8AKVU`, Intel Atom x5-Z8350 with ~2GB RAM, Python 3.13.7, Mega 2560 on COM6 at last check; now powered off.
- Preserve the existing sorter code in `Current version/` untouched until bench tested.
- Canonical sorter repository: `Mr-Azrack/Moss-Machines-Magic-the-Gathering-sorting`, main, firmware protocol 6. The older `D:\\MossLotus` installation must not be silently overwritten.
