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
