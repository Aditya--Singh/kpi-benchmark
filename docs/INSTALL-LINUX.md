# Installing and running on Linux (Ubuntu / GB10)

This is the reference installation. It runs on the NVIDIA GB10 (Ubuntu 24.04, ARM64, Python 3.12) and on any
Ubuntu 22.04 or newer machine, x86-64 or ARM64. Allow 20 minutes plus the time to download the AI model.

Contents: [Before you start](#before-you-start) · [1. Get the code](#1-get-the-code) ·
[2. Set up](#2-set-up-once) · [3. Start](#3-start-the-app) · [4. First collection](#4-first-data-collection) ·
[5. Approve](#5-approve-the-values) · [Stopping](#stopping-the-app) · [Keep it running](#keep-it-running-after-a-reboot) ·
[HTTPS](#optional-https-with-nginx) · [Updating](#updating-to-a-new-version) · [Backup](#backup-and-restore) ·
[Settings](#settings-reference) · [Troubleshooting](#troubleshooting)

## Before you start

- [ ] Python 3.10 or newer and the venv module: `sudo apt update && sudo apt install -y python3-venv python3-pip git`
- [ ] The machine can open the five bank investor pages (or you will upload the PDFs by hand)
- [ ] The machine can reach the Python package index, directly or through the bank's proxy or mirror
- [ ] Optional: Ollama for the local AI model. On the GB10 it is usually installed; check with `ollama --version`.
      To install: `curl -fsSL https://ollama.com/install.sh | sh`

## 1. Get the code

From Git (replace the address with your repository's):
```bash
cd ~
git clone https://<git-server>/<group>/pnb-kpi-bench.git
cd pnb-kpi-bench
```
Or from a zip file: unzip it and `cd` into the folder that contains `setup.sh`.

## 2. Set up (once)

```bash
./setup.sh
```
It does five things and is safe to run again at any time:
1. checks Python;
2. creates a private Python environment in `.venv` and installs the libraries;
3. creates the settings file `.env` with a fresh secret key;
4. downloads the AI models with Ollama, if Ollama is installed (several GB the first time; skip with `SKIP_MODELS=1 ./setup.sh`);
5. runs a self-test in a temporary folder.

It must end with **Self-test PASSED**. If it stops while downloading libraries, it prints whether the package
index is reachable and what to do; see [Troubleshooting](#troubleshooting).

## 3. Start the app

```bash
./run.sh
```
It prints the address to open, for example `http://10.x.x.x:8077`. Keep this terminal open: closing it stops
the app. To use it from other computers, open the port once: `sudo ufw allow 8077/tcp`.

If port 8077 is already used by another program, `run.sh` picks the next free port and tells you which.
To fix the port permanently, set `PORT=` in `.env`.

## 4. First data collection

1. Open the address and sign in as **analyst** (password from `DEFAULT_PASSWORD` in `.env`, default `Pnb@12345`).
2. Go to **Data & approvals**, then **Collect data**.
3. Keep all five banks, **5 years**, and **The banks' investor-relations websites**.
4. Leave **Let the local language model read KPIs the rules missed** unticked. With it ticked the run asks the
   model one question per missing KPI per document and can take hours; use it later for a single bank or quarter.
5. Press **Start collection** and open **Agents & logs** to watch each agent.

On the GB10 a five-year run found 118 PDFs. Downloading took about 17 minutes (the scraper waits between
requests on purpose); reading and checking them without the model takes a few minutes. Later runs download only new PDFs.

## 5. Approve the values

1. Sign out (top-right menu) and sign in as **approver**.
2. **Data & approvals**, then **Review & approve**, then **Approve all clean values**. This accepts every value that
   passed all automatic checks.
3. Select **Needs review** and the latest quarter. For each row press **evidence**, compare with the highlighted
   page text, then approve (✓), reject (✗ with a reason) or correct (✎ with a reason). Repeat for the previous quarter.
4. Press F5. The quarter list in the top bar now shows the quarters; sign in as **gm** to see the management view.

Then change the four starter passwords (top-right menu, Change password) and add named users:
```bash
source .venv/bin/activate
python -m app.cli add-user rkumar checker --name "R Kumar"
```

## Stopping the app

Press **Ctrl+C** in the terminal running it. It stops within about five seconds, even during a collection run.
An unfinished run is marked "interrupted"; start a new run and it continues from where it stopped (downloaded
PDFs and finished documents are kept).

If a terminal was closed and the port still seems busy: `fuser -k 8077/tcp`.

## Keep it running after a reboot

Use the service file in `deploy/`:
```bash
cd ~/pnb-kpi-bench                                   # the folder that contains run.sh
sudo cp deploy/kpibench.service /etc/systemd/system/
sudo sed -i "s#/home/USER/pnb-kpi-bench#$(pwd)#g; s#User=USER#User=$(whoami)#" /etc/systemd/system/kpibench.service
sudo systemctl daemon-reload
sudo systemctl enable --now kpibench
systemctl status kpibench          # should say "active (running)"
journalctl -u kpibench -f          # live log; Ctrl+C leaves the log, not the app
```
Stop or restart it with `sudo systemctl stop kpibench` / `sudo systemctl restart kpibench`.
Do not run `./run.sh` at the same time as the service.

## Optional: HTTPS with nginx

Needed when the Top Management Dashboard is served over HTTPS and shows this app inside a frame, and
recommended for production. Example `/etc/nginx/sites-available/peer-benchmarking`:
```nginx
server {
    listen 443 ssl;
    server_name peer-benchmarking.pnb.internal;
    ssl_certificate     /etc/ssl/certs/peer-benchmarking.crt;
    ssl_certificate_key /etc/ssl/private/peer-benchmarking.key;

    location / {
        proxy_pass http://127.0.0.1:8077;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        client_max_body_size 64m;
    }
    location /api/obs/stream {            # live log feed: must not be buffered
        proxy_pass http://127.0.0.1:8077;
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```
Then set `COOKIE_SECURE=true` and `HOST=127.0.0.1` in `.env` (so only nginx can reach the app) and restart.

## Updating to a new version

```bash
cd ~/pnb-kpi-bench
git pull
./setup.sh                 # installs any new libraries; keeps .env and data/
```
Then restart the app (Ctrl+C and `./run.sh`, or `sudo systemctl restart kpibench`).

## Backup and restore

Everything that matters is in two places: the `data/` folder (database, PDFs, reports, logs) and `.env`.
Stop the app, copy both somewhere safe, start the app again. To restore, stop the app, put them back, start it.

## Settings reference

All settings live in `.env`. Restart the app after changing them.

| Setting | Default | Meaning |
| --- | --- | --- |
| `PORT` | 8077 | port the app listens on |
| `HOST` | 0.0.0.0 | `0.0.0.0` = reachable from the network; `127.0.0.1` = only from this machine (behind nginx) |
| `SECRET_KEY` | set by setup | signs sessions; keep private |
| `DEFAULT_PASSWORD` | Pnb@12345 | password given to the four starter users on first start only |
| `SESSION_HOURS` | 10 | how long a sign-in lasts |
| `COOKIE_SECURE` | false | set `true` when served over HTTPS |
| `FRAME_ANCESTORS` | `'self'` | sites allowed to show the app in a frame (see INTEGRATION.md) |
| `DASHBOARD_URL`, `DASHBOARD_NAME` | empty | adds a "back to dashboard" link; Sign out returns there |
| `HANDOFF_SECRET` and other `HANDOFF_*` | off | one-click sign-in from the dashboard (see INTEGRATION.md) |
| `LLM_PROVIDER` | ollama | `ollama`, `openai_compatible` (vLLM) or `none` (template mode) |
| `OLLAMA_MODEL`, `EMBED_MODEL` | qwen2.5:14b, nomic-embed-text | local models |
| `ALLOW_REMOTE_LLM` | false | keep false: refuses any model server that is not on this machine |
| `REQUEST_DELAY_SEC` | 1.5 | pause between requests to the same bank site |
| `HISTORY_QUARTERS` | 20 | default history window (5 years) |
| `DATA_DIR`, `DB_URL` | ./data, SQLite | where data lives; `DB_URL` can point to PostgreSQL |

## Troubleshooting

| What you see | Cause and fix |
| --- | --- |
| Another application opens at the address | That port belongs to another program. `run.sh` now moves to a free port and prints it; set `PORT=` in `.env` to keep it. |
| `No matching distribution found for ...` during setup | pip cannot see that library on the index it uses. Run `python -m app.cli netcheck` (with `.venv` active) and `pip config list`. Behind a proxy: `export HTTPS_PROXY=http://<proxy>:<port>` then `./setup.sh`. Private mirror: ask for the library to be added, or `PIP_INDEX_URL=https://pypi.org/simple ./setup.sh`. |
| `SSL: CERTIFICATE_VERIFY_FAILED` during setup | The bank's network inspects HTTPS. Get the bank's root certificate (PEM) from IT and run `.venv/bin/pip config set global.cert /path/to/bank-root.pem`, then `./setup.sh`. |
| Collection seems stuck at KPI Extractor and "LLM calls" keeps growing | The model fallback was ticked; it is slow, not stuck. Stop the app (Ctrl+C), start it, and run the collection again with the box unticked. |
| Top bar says **Template mode** | The model is not reachable. `ollama list` should show the model; if not, `ollama pull qwen2.5:14b`. Restart Ollama with `sudo systemctl restart ollama`, then refresh the page. |
| A bank shows no documents | Run `python -m app.cli discover`. If the site is blocked or changed, upload the PDFs in **Data & approvals, Sources & upload** and run collection with "Only PDFs already placed in the inbox". |
| "Account locked" | Wait 15 minutes, or `python -m app.cli reset-password <user>`. |
| A document shows image-only pages | Those slides are pictures with no text. The approver enters the value with **Enter a missing value**, citing the page. |
| The page loads but shows "No data yet" | Nothing is approved yet. Approve values as **approver** (step 5). |
