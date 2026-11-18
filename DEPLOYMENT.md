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

## 4. One-time setup: Push notifications

Phone and desktop notifications for the installed apps (APS dashboard and Gas Guard): new ticket messages and low gas. They use standard Web Push. No outside service or account is needed, only a key pair that you make once.

Do 4.1 to 4.4 **once per server**. Until they're done, everything else works and the Notifications settings say "not set up on the server yet".

### 4.1 Python package

Covered by the deploy (`pip install -r requirements.txt`). Adds `pywebpush` and the packages it needs. `migrate` (also in the deploy) adds the device table.

### 4.2 Make the keys

Connect to the server (`ssh root@<server>`), then:

```bash
cd /root/aps_web/backend
source venv/bin/activate
python manage.py generate_vapid_keys --subject mailto:support@alter-power.co.za
```

Use a real APS mailbox after `mailto:`. Push services (Google, Apple, Mozilla) use it to contact you if something is wrong.

It prints three lines like these:

```
VAPID_PUBLIC_KEY=BKx...long...
VAPID_PRIVATE_KEY=q3F...
VAPID_SUBJECT=mailto:support@alter-power.co.za
```

Keep the terminal open for the next step.

**Make the keys only once.** New keys switch off notifications on every phone, and people have to turn them on again. Never share or commit the private key.

### 4.3 Add them to `.env`

```bash
nano /root/aps_web/backend/.env
```

1. Go to the bottom of the file (arrow keys).
2. Paste the three lines (right-click or Ctrl+Shift+V).
3. Save: Ctrl+O, then Enter. Exit: Ctrl+X.

While the file is open, check the `CELERY_EAGER` line. It must be:

```
CELERY_EAGER=False
```

If it's missing or `True`, background jobs run inside the web request. Ticket notifications then can't wait the ~5 seconds that lets them skip someone who already has the chat open.

### 4.4 Restart and check

```bash
systemctl restart gunicorn daphne celery-worker celery-beat
cd /root/aps_web/backend && source venv/bin/activate
python manage.py shell -c "from apps.push.send import is_configured; print(is_configured())"
```

The last command must print `True`. If it prints `False`, a line in `.env` is missing or misspelled.

Gas Guard admins also get low-gas notifications. To see who they are: `python manage.py gg_staff --list`.

### 4.5 Test on a real phone

The frontend build with the Notifications settings must be deployed first. Notifications only work in the **installed app**, not in a normal browser tab.

**Android (Chrome):**
1. Open the site in Chrome, sign in at `/dashboard`, and tap **Install the APS app** in the sidebar (or Chrome menu → Add to Home screen / Install app).
2. Open the app from the home screen.
3. Account settings → Notifications → **Turn on** → Allow.
4. Tap **Send a test notification**. It should appear within a few seconds.

**iPhone / iPad** (needs iOS / iPadOS 16.4 or newer: Settings → General → About):
1. Open the site in **Safari**, sign in, tap Share → **Add to Home Screen**.
2. Open the app from the Home Screen icon (not from Safari).
3. Same steps 3 and 4 as Android.

**Ticket message:** with notifications on for a client account on the phone, close the app. From a computer, signed in as staff, write in that client's ticket chat. The phone should show "New message on APS-…" within about 5 seconds. Tapping it opens the ticket. It also works the other way round, with notifications on for a staff account.

**Gas Guard:** open `/gas-guard`, sign in, install the Gas Guard app the same way. In Settings → Notifications, turn on and send a test. A real low-gas notification comes from the hourly check, once, when a cylinder drops to 30% or below.

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| PDF fails with "no browser" / executable not found | Chromium installed by a different user | Re-run 3.2 as root |
| PDF text looks wrong / different font | Carlito missing | Run 3.3 |
| Roof image upload fails with 413 | Nginx body size limit | Run 3.4 |
| Uploaded images don't display | `/media/` not served | Check Nginx `location /media/` |
| Live chat shows old behaviour after deploy | Daphne not restarted | `systemctl restart daphne` |
| Prices & settings empty | Catalogue not imported | Run 3.5 |
| Notifications settings say "not set up on the server yet" | VAPID keys missing in `.env`, or services not restarted | Run 4.2–4.4 |
| Test notification works, but ticket notifications never arrive | Celery worker down | `systemctl status celery-worker`, then `journalctl -u celery-worker -n 50` |
| Notifications arrive even while the chat is open | `CELERY_EAGER` not `False` | Fix it in `.env` (4.3), then restart (4.4) |
| Notifications stopped on every phone at once | Keys were changed | Everyone turns notifications on again in settings |
| iPhone has no "Turn on" button | Opened in Safari, not from the Home Screen icon, or iOS older than 16.4 | See 4.5 |
