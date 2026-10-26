"""Local LLM client (Ollama or any OpenAI-compatible server such as vLLM).

Data never leaves the machine: remote endpoints are refused unless ALLOW_REMOTE_LLM=true.
Every call is recorded (purpose, model, prompt version, latency, sizes) for observability and
model-risk governance. If no LLM is reachable the app keeps working in deterministic template mode.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

import httpx

from .config import settings
from .observability import log, record_llm_call
from .security import is_local_url, redact_pii

PROMPTS: dict[str, str] = {}  # purpose -> template (filled by modules); version = sha1 prefix


def prompt_version(template: str) -> str:
    return "p-" + hashlib.sha1(template.encode()).hexdigest()[:10]


class LLM:
    def __init__(self) -> None:
        self.provider = settings.LLM_PROVIDER.lower()
        self._status_cache: tuple[float, dict] | None = None

    @property
    def model(self) -> str:
        return settings.OLLAMA_MODEL if self.provider == "ollama" else settings.OPENAI_COMPAT_MODEL

    def _base(self) -> str:
        return settings.OLLAMA_URL if self.provider == "ollama" else settings.OPENAI_COMPAT_URL

    def status(self, force: bool = False) -> dict:
        if self._status_cache and not force and time.time() - self._status_cache[0] < 30:
            return self._status_cache[1]
        st = {"provider": self.provider, "model": self.model, "available": False, "models": [], "reason": ""}
        if self.provider == "none":
            st["reason"] = "LLM disabled (template mode)"
        elif not settings.ALLOW_REMOTE_LLM and not is_local_url(self._base()):
            st["reason"] = "Refused: LLM endpoint is not local and ALLOW_REMOTE_LLM is false"
        else:
            try:
                if self.provider == "ollama":
                    r = httpx.get(f"{settings.OLLAMA_URL}/api/tags", timeout=4)
                    names = [m["name"] for m in r.json().get("models", [])]
                    st["models"] = names
                    st["available"] = (self.model in names or f"{self.model}:latest" in names
                                       or (":" not in self.model and any(n.split(":")[0] == self.model for n in names)))
                    if not st["available"]:
                        st["reason"] = f"Model '{self.model}' not pulled. Run: ollama pull {self.model}"
                    st["embed_available"] = any(n.split(":")[0] == settings.EMBED_MODEL.split(":")[0] for n in names)
                else:
                    r = httpx.get(f"{settings.OPENAI_COMPAT_URL}/models", timeout=4)
                    st["models"] = [m["id"] for m in r.json().get("data", [])]
                    st["available"] = bool(st["models"])
                    st["embed_available"] = False
            except Exception as exc:
                st["reason"] = f"LLM server not reachable ({type(exc).__name__}). Start it with: ollama serve"
        self._status_cache = (time.time(), st)
        return st

    @property
    def available(self) -> bool:
        return bool(self.status().get("available"))

    def chat(self, system: str, user: str, purpose: str, template: str | None = None, json_mode: bool = False,
             temperature: float = 0.1, max_tokens: int = 900) -> str | None:
        if not self.available:
            return None
        user, pii = redact_pii(user)
        if pii:
            log.warning("PII redacted before LLM call (%s): %s", purpose, ",".join(pii))
        pv = prompt_version(template or system)
        t0 = time.perf_counter()
        text, err, ptok, ctok = None, None, None, None
        try:
            if self.provider == "ollama":
                body = {"model": self.model, "stream": False,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 8192}}
                if json_mode:
                    body["format"] = "json"
                r = httpx.post(f"{settings.OLLAMA_URL}/api/chat", json=body, timeout=settings.LLM_TIMEOUT)
                r.raise_for_status()
                d = r.json()
                text = d.get("message", {}).get("content", "")
                ptok, ctok = d.get("prompt_eval_count"), d.get("eval_count")
            else:
                body = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
                if json_mode:
                    body["response_format"] = {"type": "json_object"}
                r = httpx.post(f"{settings.OPENAI_COMPAT_URL}/chat/completions", json=body, timeout=settings.LLM_TIMEOUT)
                r.raise_for_status()
                d = r.json()
                text = d["choices"][0]["message"]["content"]
                u = d.get("usage") or {}
                ptok, ctok = u.get("prompt_tokens"), u.get("completion_tokens")
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            log.warning("LLM call failed (%s): %s", purpose, err)
        lat = int((time.perf_counter() - t0) * 1000)
        record_llm_call(purpose=purpose, provider=self.provider, model=self.model, prompt_version=pv,
                        prompt_chars=len(system) + len(user), completion_chars=len(text or ""),
                        prompt_tokens=ptok, completion_tokens=ctok, latency_ms=lat, ok=err is None, error=err)
        return text

    def chat_json(self, system: str, user: str, purpose: str, template: str | None = None) -> dict | None:
        raw = self.chat(system, user, purpose, template=template, json_mode=True, temperature=0.0)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
        return None

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        st = self.status()
        if self.provider != "ollama" or not st.get("embed_available"):
            return None
        try:
            r = httpx.post(f"{settings.OLLAMA_URL}/api/embed", json={"model": settings.EMBED_MODEL, "input": texts},
                           timeout=settings.LLM_TIMEOUT)
            r.raise_for_status()
            return r.json().get("embeddings")
        except Exception as exc:
            log.warning("Embedding call failed: %s", exc)
            return None


llm = LLM()
