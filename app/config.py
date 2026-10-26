"""Central configuration. Values come from environment variables (or a .env file)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = BASE_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    APP_NAME = "PNB KPI Benchmarking"
    APP_VERSION = "1.0.0"

    DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
    DB_URL = os.getenv("DB_URL", f"sqlite:///{(DATA_DIR / 'kpibench.db').as_posix()}")
    RAW_DIR = DATA_DIR / "raw"          # downloaded PDFs (content-addressed)
    INBOX_DIR = DATA_DIR / "inbox"      # manual drop folder: data/inbox/<BANK>/*.pdf
    OUTPUT_DIR = DATA_DIR / "outputs"   # generated reports
    LOG_DIR = DATA_DIR / "logs"
    SOURCES_FILE = Path(os.getenv("SOURCES_FILE", BASE_DIR / "config" / "sources.yaml"))
    STATIC_DIR = BASE_DIR / "static"

    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8077"))

    # Security
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    SESSION_HOURS = int(os.getenv("SESSION_HOURS", "10"))
    COOKIE_SECURE = _bool("COOKIE_SECURE", False)  # set true behind HTTPS
    # e.g. 'self' https://dashboard.pnb.internal  (quotes optional: self/none are normalised to CSP keywords)
    FRAME_ANCESTORS = " ".join(f"'{t}'" if t.lower() in ("self", "none") else t
                               for t in os.getenv("FRAME_ANCESTORS", "'self'").replace("'", " ").replace('"', " ").split()) or "'self'"
    DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "Pnb@12345")

    # Top Management Dashboard integration (see docs/INTEGRATION.md)
    DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").strip()          # shows a "back to dashboard" link
    DASHBOARD_NAME = os.getenv("DASHBOARD_NAME", "Top Management Dashboard").strip()
    HANDOFF_SECRET = os.getenv("HANDOFF_SECRET", "").strip()        # empty = one-click sign-in disabled
    HANDOFF_AUTO_CREATE = _bool("HANDOFF_AUTO_CREATE", True)        # unknown dashboard users become viewers
    HANDOFF_ALLOWED_IPS = [x.strip() for x in os.getenv("HANDOFF_ALLOWED_IPS", "").split(",") if x.strip()]
    HANDOFF_TICKET_SECONDS = int(os.getenv("HANDOFF_TICKET_SECONDS", "120"))
    HANDOFF_ALLOW_STAFF = _bool("HANDOFF_ALLOW_STAFF", False)        # analysts/approvers/admins use passwords

    # LLM (local only by default -> no data egress)
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")  # ollama | openai_compatible | none
    OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
    OPENAI_COMPAT_URL = os.getenv("OPENAI_COMPAT_URL", "http://localhost:8001/v1")  # e.g. vLLM
    OPENAI_COMPAT_MODEL = os.getenv("OPENAI_COMPAT_MODEL", "Qwen/Qwen2.5-14B-Instruct")
    EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
    LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "180"))
    ALLOW_REMOTE_LLM = _bool("ALLOW_REMOTE_LLM", False)

    # Scraper
    USER_AGENT = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0 Safari/537.36 PNB-KPI-Benchmarking/1.0 (internal research)",
    )
    RESPECT_ROBOTS = _bool("RESPECT_ROBOTS", True)
    REQUEST_DELAY_SEC = float(os.getenv("REQUEST_DELAY_SEC", "1.5"))
    HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "60"))
    HISTORY_QUARTERS = int(os.getenv("HISTORY_QUARTERS", "20"))
    EXTRACT_WORKERS = int(os.getenv("EXTRACT_WORKERS", str(max(1, (os.cpu_count() or 2) // 2))))

    HOME_BANK = os.getenv("HOME_BANK", "PNB")

    def ensure_dirs(self) -> None:
        for d in (self.DATA_DIR, self.RAW_DIR, self.INBOX_DIR, self.OUTPUT_DIR, self.LOG_DIR):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
