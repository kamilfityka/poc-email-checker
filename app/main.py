"""
Serwis walidatora - FastAPI (jeden kontener, §3 spec).

Endpointy:
  POST /validate            - L2-L4 + kontrakt §6
  POST /validate/csv        - walidacja wsadowa z pliku CSV (raport JSON/CSV)
  GET  /healthz             - health check
  GET  /config              - podglad aktywnej polityki (debug)
  GET  /batch               - UI wgrywania CSV (static/batch.html)
  GET  /                     - demo formularza CRM (static/demo.html)
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Header, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, cache, crm, runtime, name_match, ai_client, batch
from .models import (
    ValidateRequest,
    ValidateResponse,
    AdminSettingsRequest,
)
from .validation import validate as run_validation, message_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("validator")

app = FastAPI(
    title="Walidator e-mail (PoC v2)",
    version="2.0.0",
    description="Real-time walidacja adresu e-mail - warstwy L2-L4 + L6, bez uslug zewnetrznych.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_STATIC = Path(__file__).parent.parent / "static"


def _mask_email(email: str) -> str:
    """§13: nie logujemy pelnych adresow (o ile LOG_FULL_EMAIL nie wlaczone)."""
    if config.LOG_FULL_EMAIL:
        return email
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    head = local[0] if local else ""
    return f"{head}***@{domain}"


@app.post("/validate", response_model=ValidateResponse)
def validate_endpoint(req: ValidateRequest) -> ValidateResponse:
    raw = run_validation(req.email, req.checks)

    # Polityka blokowania - poza kodem walidacji (§8, config.py).
    policy = config.resolve_block(raw["result"])

    message = message_for(
        raw["result"],
        domain=(req.email.split("@")[-1] if "@" in req.email else ""),
        suggestion=(raw["suggestion"].split("@")[-1] if raw.get("suggestion") else ""),
    )

    # Warstwy opcjonalne uruchamiamy tylko dla adresow poprawnych skladniowo -
    # dla malformed nie ma sensu pytac bazy/DNS/serwera. Kazda zwraca None gdy
    # wylaczona i ZADNA nie wplywa na block_save (§6 - sygnal informacyjny).
    syntax_ok = raw["syntax_valid"]

    # (1) Deduplikacja w CRM.
    exists_in_crm = crm.email_exists(req.email) if syntax_ok else None

    # (2) Zgodnosc imie/nazwisko <-> adres: heurystyka + opcjonalne AI.
    name_match_res = _evaluate_name_match(req.name, req.email) if syntax_ok else None

    logger.info(
        "validate email=%s result=%s block_save=%s cached=%s crm=%s name=%s %sms",
        _mask_email(req.email),
        raw["result"],
        policy["block_save"],
        raw["cached"],
        exists_in_crm,
        (name_match_res or {}).get("status"),
        raw["elapsed_ms"],
    )

    return ValidateResponse(
        email=raw["email"],
        result=raw["result"],
        block_save=policy["block_save"],
        block_override_allowed=policy["override_allowed"],
        syntax_valid=raw["syntax_valid"],
        domain_status=raw["domain_status"],
        has_mx=raw["has_mx"],
        disposable=raw["disposable"],
        role_based=raw["role_based"],
        suggestion=raw["suggestion"],
        message_pl=message,
        cached=raw["cached"],
        elapsed_ms=raw["elapsed_ms"],
        exists_in_crm=exists_in_crm,
        name_email_match=(name_match_res or {}).get("status"),
        name_suggestion=(name_match_res or {}).get("suggestion"),
        name_match_source=(name_match_res or {}).get("source"),
    )


def _evaluate_name_match(name: Optional[str], email: str) -> Optional[dict]:
    """Heurystyka zgodnosci imie<->email + opcjonalne dopracowanie AI.

    Zwraca dict {status, suggestion, source} albo None gdy brak 'name' / warstwa
    wylaczona. AI dziala tylko gdy wlaczony przelacznik 'ai' i skonfigurowany
    model; przy bledzie zostaje wynik heurystyki.
    """
    if not (name or "").strip() or not config.NAME_MATCH_ENABLED:
        return None
    result = name_match.evaluate(name, email)
    refined = ai_client.refine_name_match(name, email, result)
    return refined or result


@app.post("/validate/csv")
async def validate_csv_endpoint(
    file: UploadFile = File(..., description="Plik CSV: id,email,imie,nazwisko"),
    format: str = Query("json", pattern="^(json|csv)$", description="Format raportu"),
    checks: Optional[str] = Query(
        None, description="Warstwy po przecinku, np. 'syntax,typo,lists' (domyslnie wszystkie)"
    ),
    name_match_on: bool = Query(True, alias="name_match", description="Licz zgodnosc imie<->email"),
):
    """Walidacja wsadowa: wgrywasz CSV, dostajesz raport dla calej listy.

    Te same warstwy i ta sama polityka blokowania co w /validate - batch niczego
    nie luzuje ani nie zaostrza. Duplikaty adresu liczone raz (oznaczone w raporcie).
    `format=csv` zwraca gotowy plik do pobrania, `json` - wiersze + podsumowanie.
    """
    raw = await file.read()
    if len(raw) > config.BATCH_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Plik za duzy ({len(raw)} B). Limit: {config.BATCH_MAX_BYTES} B",
        )

    check_list = [c.strip() for c in checks.split(",") if c.strip()] if checks else None
    if check_list:
        unknown = [c for c in check_list if c not in ("syntax", "typo", "dns", "mx", "lists")]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Nieznane warstwy: {', '.join(unknown)}")

    try:
        report = batch.run(raw, checks=check_list, with_name_match=name_match_on)
    except batch.CsvFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    summary = report["summary"]
    logger.info(
        "validate/csv file=%s rows=%s valid=%s blocked=%s %sms",
        file.filename,
        summary["total_rows"],
        summary["valid"],
        summary["blocked"],
        summary["elapsed_ms"],
    )

    if format == "csv":
        return Response(
            content=batch.to_csv(report["rows"]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="raport-walidacji.csv"'},
        )
    return report


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/config")
def show_config() -> dict:
    """Podglad aktywnej polityki + cache (debug/transparentnosc)."""
    return {
        "result_policy": config.RESULT_POLICY,
        "block_modes": config.BLOCK_MODES,
        "dns_timeout_s": config.DNS_TIMEOUT_S,
        "cache": cache.stats(),
        # Warstwy opcjonalne + ich runtime-przelaczniki (panel /admin).
        "toggles": runtime.states(),
        "crm": crm.stats(),
        "ai": ai_client.stats(),
    }


# --- Panel administracyjny: przelaczniki warstw AI / CRM ----------------------
def _require_admin(x_admin_token: Optional[str]) -> None:
    """Gdy ADMIN_TOKEN ustawione - wymagaj naglowka X-Admin-Token. Inaczej otwarte."""
    if config.ADMIN_TOKEN and (x_admin_token or "") != config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Brak lub bledny X-Admin-Token")


def _admin_state() -> dict:
    """Stan przelacznikow + czy warstwa jest w ogole skonfigurowana."""
    return {
        "toggles": runtime.states(),
        "integrations": {
            "ai": ai_client.stats(),
            "crm": crm.stats(),
        },
        "admin_protected": bool(config.ADMIN_TOKEN),
    }


@app.get("/admin/settings")
def admin_settings_get(x_admin_token: Optional[str] = Header(default=None)) -> dict:
    _require_admin(x_admin_token)
    return _admin_state()


@app.post("/admin/settings")
def admin_settings_set(
    body: AdminSettingsRequest,
    x_admin_token: Optional[str] = Header(default=None),
) -> dict:
    _require_admin(x_admin_token)
    runtime.apply(body.model_dump(exclude_none=True))
    logger.info("admin/settings -> %s", runtime.states())
    return _admin_state()


@app.get("/admin", response_class=HTMLResponse)
def admin_panel() -> str:
    panel = _STATIC / "admin.html"
    if panel.exists():
        return panel.read_text(encoding="utf-8")
    return "<h1>Panel</h1><p>Brak admin.html</p>"


@app.get("/batch", response_class=HTMLResponse)
def batch_page() -> str:
    page = _STATIC / "batch.html"
    if page.exists():
        return page.read_text(encoding="utf-8")
    return "<h1>Walidacja wsadowa</h1><p>Brak batch.html</p>"


@app.get("/", response_class=HTMLResponse)
def demo() -> str:
    demo_file = _STATIC / "demo.html"
    if demo_file.exists():
        return demo_file.read_text(encoding="utf-8")
    return "<h1>Walidator e-mail PoC</h1><p>Brak demo.html</p>"


# Statyki (widget.js itd.)
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
