# APS Web – Deployment Notes

Server: Hostinger VPS `srv934843` (Ubuntu 24.04)
Backend path: `/root/aps_web/backend`
Everything runs as **root**.

---

## 1. Services

| Service | What it runs | Restart on deploy? |
|---|---|---|
| `gunicorn` | Django HTTP / REST API | Yes |
| `daphne` | WebSockets (live chat) | Yes |
| `celery-worker` | Background tasks | Yes |
| `celery-beat` | Scheduled tasks | Yes |
| `redis` | Celery broker + Channels layer | No |
| `nginx` | Reverse proxy, static and media files | Only when its config changes |

> `gasguard*` services belong to the Gas Guard project on the same server. Do not touch them when deploying APS.

Check a service:

```bash
systemctl status daphne
systemctl is-enabled daphne      # must be "enabled" so it starts after a reboot
journalctl -u daphne -n 50       # last 50 log lines
```

---

## 2. Every deploy (automated by GitHub Actions)

The CD workflow runs these on the server:

```bash
git pull
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
systemctl restart gunicorn daphne celery-worker celery-beat
```

**Important:** `daphne` must be in the restart line. Gunicorn and Daphne are separate processes. If only Gunicorn restarts, live chat keeps running the old code.

The React frontend build is deployed separately.

---

## 3. One-time setup: Solar Estimate feature

These steps are done **once per server**. They are not part of the CD workflow. Repeat them only when setting up a new server.

### 3.1 Python packages

Covered by the deploy (`pip install -r requirements.txt`). Adds `playwright` and `openpyxl`.

### 3.2 PDF browser (Playwright Chromium)

PDFs are rendered by a headless Chromium through Playwright.

The browser is installed into the home folder of the user who runs the command, so it **must be installed by the same user that runs Gunicorn** (root on this server). If another user installs it, PDF generation fails with a "no browser" error.

```bash
cd /root/aps_web/backend
venv/bin/playwright install-deps chromium   # system libraries
venv/bin/playwright install chromium        # the browser itself
```

Alternative: set `PLAYWRIGHT_BROWSERS_PATH` in the Gunicorn service environment to a shared folder.

### 3.3 Font (Carlito)

The PDF uses Carlito, a free font metric-compatible with Calibri.

```bash
apt install fonts-crosextra-carlito
fc-list | grep -i carlito     # should list Regular, Bold, Italic, Bold Italic
```

### 3.4 Nginx upload size

Nginx rejects request bodies over 1 MB by default, which blocks roof layout image uploads.

In the APS site config (`/etc/nginx/sites-enabled/<aps-site>`), inside the `server { }` block:

```nginx
client_max_body_size 12M;
```

Then test and reload:

```bash
nginx -t && systemctl reload nginx
```

Also confirm `/media/` is served by Nginx so uploaded roof images display.

### 3.5 Load the equipment catalogue

Imports equipment, prices and tariffs from the Excel template into **Prices & settings**.

```bash
# copy APS_Solar_Estimate_TEMPLATE.xlsx to the backend folder first
cd /root/aps_web/backend
source venv/bin/activate
python manage.py import_estimate_template APS_Solar_Estimate_TEMPLATE.xlsx
rm APS_Solar_Estimate_TEMPLATE.xlsx
```

After this, prices are maintained in the admin under **Prices & settings**. Re-running the import (or uploading a new workbook in Prices & settings) replaces the catalogue and tariffs.

### 3.6 Restart and deploy

Restart services (or push to trigger the CD workflow) and deploy the frontend build.

### 3.7 Smoke test

1. Create a proposal in the admin dashboard.
2. Confirm it.
3. Download the PDF and check it has **5 pages** and uses the correct font.
4. Upload a roof layout image and check it appears.

---

## 4. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| PDF fails with "no browser" / executable not found | Chromium installed by a different user | Re-run 3.2 as root |
| PDF text looks wrong / different font | Carlito missing | Run 3.3 |
| Roof image upload fails with 413 | Nginx body size limit | Run 3.4 |
| Uploaded images don't display | `/media/` not served | Check Nginx `location /media/` |
| Live chat shows old behaviour after deploy | Daphne not restarted | `systemctl restart daphne` |
| Prices & settings empty | Catalogue not imported | Run 3.5 |

