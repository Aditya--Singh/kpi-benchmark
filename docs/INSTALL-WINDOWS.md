# Installing and running on Windows

For Windows 10, Windows 11 and Windows Server 2019 or newer. The application is the same Python program as on
Linux; only the setup and start scripts differ (`setup.bat` and `run.bat`). Allow 20 minutes.

Contents: [Before you start](#before-you-start) · [1. Install Python](#1-install-python) ·
[2. Get the code](#2-get-the-code) · [3. Set up](#3-set-up-once) · [4. Start](#4-start-the-app) ·
[5. First collection and approval](#5-first-data-collection-and-approval) · [AI model](#optional-the-local-ai-model) ·
[Manual setup](#manual-setup-if-the-bat-files-are-blocked) · [Keep it running](#keep-it-running-without-a-signed-in-user) ·
[Updating](#updating-to-a-new-version) · [Troubleshooting](#troubleshooting)

## Before you start

- [ ] Administrator rights, only for installing Python and opening the firewall port
- [ ] The PC can open the five bank investor pages (or you will upload the PDFs by hand)
- [ ] The PC can reach the Python package index, directly or through the bank's proxy or mirror
- [ ] A folder with a short path that is not synchronised by OneDrive, for example `C:\Apps`

## 1. Install Python

1. Download Python 3.12 (64-bit) from https://www.python.org/downloads/windows/.
2. Run the installer. On the first screen **tick "Add python.exe to PATH"**, then **Install Now**.
3. If the last screen offers **Disable path length limit**, click it.
4. Open PowerShell (Start menu, type PowerShell) and type `py --version`. It should print `Python 3.12.x`.

If typing `python` opens the Microsoft Store instead, go to **Settings, Apps, Advanced app settings,
App execution aliases** and switch off the two "python" entries.

## 2. Get the code

From Git, with Git for Windows installed (https://git-scm.com/download/win), in PowerShell:
```powershell
cd C:\Apps
git clone https://<git-server>/<group>/pnb-kpi-bench.git
cd pnb-kpi-bench
```
Or from a zip file: right-click the zip, **Properties**, tick **Unblock**, **OK**; then right-click,
**Extract All** to `C:\Apps`. Unblocking first stops Windows from blocking the scripts inside.

## 3. Set up (once)

Open the folder `C:\Apps\pnb-kpi-bench` in File Explorer and double-click **setup.bat**.
A window opens and works through five steps:
1. finds Python 3.10 or newer;
2. creates a private Python environment in `.venv` and installs the libraries;
3. creates the settings file `.env` with a fresh secret key;
4. downloads the AI models if Ollama is installed;
5. runs a self-test in a temporary folder.

It must end with **Self-test PASSED**, then press any key to close the window.
If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.

## 4. Start the app

Double-click **run.bat**. The window prints the address, for example `http://10.x.x.x:8077`.
Keep the window open while the app is in use; closing it (or Ctrl+C) stops the app.

The first time, Windows Defender Firewall may ask whether Python may accept connections: tick **Private** and
**Domain** networks and click **Allow**. To open the port yourself, in PowerShell **run as administrator**:
```powershell
New-NetFirewallRule -DisplayName "PNB Peer Benchmarking" -Direction Inbound -Protocol TCP -LocalPort 8077 -Action Allow
```
If port 8077 is used by another program, `run.bat` picks the next free port and prints it.
To keep a port permanently, open `.env` in Notepad and change `PORT=`.

## 5. First data collection and approval

These steps are done in the browser and are the same on every system:

1. Sign in as **analyst** (password from `DEFAULT_PASSWORD` in `.env`, default `Pnb@12345`).
2. **Data & approvals**, **Collect data**: keep five banks, 5 years and the websites; leave
   **Let the local language model read KPIs the rules missed** unticked; press **Start collection**.
3. Watch **Agents & logs**. The first run downloads about 120 PDFs, which takes 15 to 30 minutes.
4. Sign out, sign in as **approver**: **Review & approve**, **Approve all clean values**, then check the
   **Needs review** items for the latest quarters (evidence, then ✓, ✗ or ✎).
5. Press F5, sign in as **gm**, and use Overview, Trends, Peer comparison and Ask Myra.
6. Change the four starter passwords (top-right menu, Change password).

## Optional: the local AI model

1. Install Ollama for Windows from https://ollama.com/download/windows.
2. In PowerShell: `ollama pull qwen2.5:14b` and `ollama pull nomic-embed-text`.
3. Restart `run.bat`. The top bar shows **Local LLM ready**.

The 14-billion-parameter model needs a recent NVIDIA GPU with 12 GB or more of memory to answer in seconds.
On a PC without one, leave Ollama out and set `LLM_PROVIDER=none` in `.env`: the app runs in template mode with
the same numbers and plainer wording.

## Manual setup (if the .bat files are blocked)

In PowerShell, inside the project folder:
```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env                                   # review the settings; SECRET_KEY may stay empty (the app then creates one)
.\.venv\Scripts\python.exe -m app.cli selftest
.\.venv\Scripts\python.exe -m app.main         # starts the app; Ctrl+C stops it
```
To run the PowerShell scripts directly instead of the .bat files:
`powershell -ExecutionPolicy Bypass -File .\setup.ps1` and `powershell -ExecutionPolicy Bypass -File .\run.ps1`.

## Keep it running without a signed-in user

Use Task Scheduler so the app starts with Windows:

1. Open **Task Scheduler**, **Create Task** (not "Basic Task").
2. **General**: name `PNB Peer Benchmarking`; choose **Run whether user is logged on or not**.
3. **Triggers**: **New**, **At startup**.
4. **Actions**: **New**, **Start a program**:
   - Program: `C:\Apps\pnb-kpi-bench\.venv\Scripts\python.exe`
   - Arguments: `-m app.main`
   - Start in: `C:\Apps\pnb-kpi-bench`
5. **Settings**: untick **Stop the task if it runs longer than**. Click **OK** and enter the account password.

The app reads its port and settings from `.env`. To stop it, end the task in Task Scheduler.

## Updating to a new version

```powershell
cd C:\Apps\pnb-kpi-bench
git pull
```
Then double-click **setup.bat** (installs any new libraries; keeps `.env` and `data\`) and restart the app.

## Backup

Stop the app and copy the `data` folder and the `.env` file somewhere safe.

## Troubleshooting

| What you see | Cause and fix |
| --- | --- |
| "Python 3.10 or newer was not found" | Install Python as in step 1 with "Add python.exe to PATH" ticked, close the window, run setup.bat again. |
| "running scripts is disabled on this system" | Use the .bat files (they start PowerShell with the right option), or the commands in Manual setup. |
| `No matching distribution found for ...` | pip cannot see that library. Run `.\.venv\Scripts\python.exe -m app.cli netcheck`. Behind a proxy, in PowerShell: `$env:HTTPS_PROXY="http://<proxy>:<port>"; $env:HTTP_PROXY=$env:HTTPS_PROXY` then `powershell -ExecutionPolicy Bypass -File .\setup.ps1` from the same window. |
| `SSL: CERTIFICATE_VERIFY_FAILED` | The bank's network inspects HTTPS. Get the bank's root certificate (PEM) from IT, then `.\.venv\Scripts\python.exe -m pip config set global.cert C:\certs\bank-root.pem` and run setup again. |
| Another application opens at the address | That port belongs to another program; `run.bat` moves to a free one and prints it. `netstat -ano \| findstr :8077` shows the process ID using a port. |
| Other PCs cannot open the address | Open the firewall port (step 4). |
| PDF reading is slow or antivirus warnings appear | Some antivirus tools block Python worker processes. The app then reads PDFs one at a time automatically; ask IT to allow `C:\Apps\pnb-kpi-bench\.venv\Scripts\python.exe` to restore full speed. |
| Errors about long file paths | Install under a short folder such as `C:\Apps`, or enable long paths (step 1). |
| Top bar says **Template mode** | Ollama is not installed or not running. This is fine; see the AI model section. |
| "Account locked" | Wait 15 minutes, or `.\.venv\Scripts\python.exe -m app.cli reset-password <user>`. |

For anything else, the Linux guide's troubleshooting table applies too: [INSTALL-LINUX.md](INSTALL-LINUX.md#troubleshooting).
