"""
Opcjonalne dopracowanie oceny "imie/nazwisko <-> adres" WLASNYM modelem AI.

Feature-flag: domyslnie WYLACZONE (AI_ENABLED). Klient mowi w standardzie
OpenAI-compatible (POST {AI_BASE_URL}/chat/completions) - pasuje do wiekszosci
self-hosted (vLLM, Ollama, LM Studio, TGI, LiteLLM, ...). Wasz wlasny model
wystarczy wystawic pod tym kontraktem i wskazac przez AI_BASE_URL/AI_MODEL.

Zasady (spojne z §6): przy jakimkolwiek problemie (wylaczone, brak httpx, timeout,
zla odpowiedz) zwracamy None -> zostaje wynik heurystyki. AI nigdy nie blokuje
i nie wywraca walidacji poprawnosci.
"""
import json
import logging
import re
from typing import Optional

from . import config

logger = logging.getLogger("validator.ai")

_SYSTEM = (
    "Jestes walidatorem CRM. Oceniasz, czy wpisane imie i nazwisko pasuja do "
    "czesci lokalnej adresu e-mail (przed @). Uwzgledniaj zdrobnienia, inicjaly, "
    "kolejnosc, polskie znaki i literowki. Odpowiadaj WYLACZNIE zwartym JSON-em: "
    '{"status":"match|partial|mismatch","suggestion":"Poprawione Imie Nazwisko lub pusty string","reason":"krotko"}. '
    "Pole suggestion wypelniaj tylko, gdy widac literowke we wpisanym imieniu/nazwisku; "
    "w przeciwnym razie zostaw pusty string."
)


def is_enabled() -> bool:
    return bool(config.AI_ENABLED and config.AI_BASE_URL and config.AI_MODEL)


def _extract_json(text: str) -> Optional[dict]:
    """Wyluskuje pierwszy obiekt JSON z odpowiedzi modelu (odporne na otoczke)."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _call(name: str, email: str) -> Optional[str]:
    """Surowe wywolanie modelu. Zwraca tresc odpowiedzi albo None."""
    import httpx  # leniwy import - zaleznosc opcjonalna

    url = f"{config.AI_BASE_URL}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.AI_API_KEY:
        headers["Authorization"] = f"Bearer {config.AI_API_KEY}"

    payload = {
        "model": config.AI_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Imie i nazwisko: {name}\nE-mail: {email}"},
        ],
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=config.AI_TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def refine_name_match(name: str, email: str, heuristic: Optional[dict] = None) -> Optional[dict]:
    """
    Zwraca dopracowany dict {"status","suggestion","source":"ai"} albo None
    (wtedy wolajacy zostaje przy wyniku heurystyki).
    """
    if not is_enabled() or not (name or "").strip() or not (email or "").strip():
        return None

    try:
        content = _call(name, email)
    except Exception:
        logger.warning("AI: wywolanie modelu nieudane (%s)", config.AI_BASE_URL, exc_info=True)
        return None

    parsed = _extract_json(content or "")
    if not parsed:
        logger.warning("AI: nie udalo sie sparsowac odpowiedzi modelu")
        return None

    status = str(parsed.get("status", "")).strip().lower()
    if status not in ("match", "partial", "mismatch"):
        return None

    suggestion = (parsed.get("suggestion") or "").strip() or None
    return {"status": status, "suggestion": suggestion, "source": "ai"}


def stats() -> dict:
    """Podglad stanu integracji (bez klucza) - dla endpointu /config."""
    return {
        "enabled": is_enabled(),
        "base_url": config.AI_BASE_URL,
        "model": config.AI_MODEL,
    }
