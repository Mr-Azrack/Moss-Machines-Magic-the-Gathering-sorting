# Roadmap

## Completed: Server foundation v0.1

- API for new scan capture (authenticated), validation and private image storage.
- Human review dashboard and exact-printing correction with explicit approval.
- Verified sample index, advisory similarity suggestions, read-only status and metrics.
- SQLite audit log and baseline security tests.

## Next: Hosting launch

- Create MOSS-specific WHM/cPanel account and DNS/subdomain; enable HTTPS and Python WSGI.
- Deploy package, install Pillow, configure secrets and private storage path.
- Verify /health, /admin and server resource limits.
- Enable backups, log inspection and plan data retention.

## Next: Shadow-mode sorter adapter

- Identify the actual live launcher (GitHub checkout vs D:\\MossLotus).
- Add a strictly opt-in asynchronous image-upload adapter to the canonical sorter recognition path **without** changing mechanics or local identity safety.
- Use short network timeouts, retries/queueing and no-stall/no-motion when offline.
- Record scanner's existing candidates, local selected ID, software versions and processing time.
- Run passive comparisons and human verification for a controlled fixed test deck.

## Then: Better exact printing recognition

- Import an authoritative product catalog with MTG set/collector/variant metadata.
- OCR for name and collector number, calibrated set-symbol verification and robust artwork-region image matching.
- Scale verified-example indexing beyond 20,000 examples.
- Add operator accounts, role-controlled correction, privacy retention, audit export.
- Benchmark a small ML embedding model only if shared-host quotas permit; otherwise use a separate inference host.
- Require demonstrated improvement against fixed ground-truth scans prior to production adoption.

## Production release gate

- Stable 50–100+ card multi-bin sorting runs with no unexpected motion.
- False positive identification rates, reject rate, latency and resource use documented.
- Verified safe reject/fallback and correct performance after network outage.
