"""
Serwis walidatora - FastAPI (jeden kontener, §3 spec).

Endpointy:
  POST /validate            - L2-L4 + kontrakt §6
  POST /verify/send         - L6 double opt-in: generuje token, wysyla mail (w tle)
  GET  /verify/confirm      - L6: potwierdza token (HTML dla przegladarki, JSON dla API)
  GET  /verify/status       - L6: czy adres jest potwierdzony
  GET  /healthz             - health check
  GET  /config              - podglad aktywnej polityki (debug)
  GET  /                     - demo formularza CRM (static/demo.html)
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, cache, verify, crm, runtime, name_match, ai_client, smtp_check
from . import verify_templates as vtpl
from .verify_store import store as verify_store
from .models import (
    ValidateRequest,
    ValidateResponse,
    VerifySendRequest,
    VerifySendResponse,
    VerifyConfirmResponse,
    VerifyStatusResponse,
    AdminSettingsRequest,
)
from .validation import validate as run_validation, message_for, check_syntax

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

    # (3) SMTP check w czasie rzeczywistym - tylko gdy domena obsluguje poczte.
    smtp_res = (
        smtp_check.check(req.email)
        if syntax_ok and raw["domain_status"] in ("ok", "not_checked")
        else None
    )

    logger.info(
        "validate email=%s result=%s block_save=%s cached=%s crm=%s name=%s smtp=%s %sms",
        _mask_email(req.email),
        raw["result"],
        policy["block_save"],
        raw["cached"],
        exists_in_crm,
        (name_match_res or {}).get("status"),
        smtp_res,
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
        smtp_check=smtp_res,
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


@app.post("/verify/send", response_model=VerifySendResponse)
def verify_send(req: VerifySendRequest, background: BackgroundTasks) -> VerifySendResponse:
    email = (req.email or "").strip().lower()

    # Skladnia musi byc poprawna, zeby w ogole probowac wyslac.
    ok, _, _ = check_syntax(email)
    if not ok:
        raise HTTPException(status_code=422, detail="Adres niepoprawny skladniowo")

    try:
        # Wysylka "w tle" (§L6) - pole formularza nie czeka na SMTP.
        status = verify.create_and_send(email, background.add_task)
    except verify.RateLimited:
        raise HTTPException(
            status_code=429,
            detail=f"Zbyt wiele prob dla tego adresu. Sprobuj pozniej "
                   f"(limit {config.VERIFY_RATE_MAX}/{config.VERIFY_RATE_WINDOW_S // 60} min).",
        )

    logger.info("verify/send email=%s -> %s", _mask_email(email), status)
    return VerifySendResponse(status=status)


@app.get("/verify/confirm")
def verify_confirm(token: str, request: Request, format: str = ""):
    """
    Potwierdza token. Domyslnie zwraca STRONE HTML (klient klika link w mailu).
    Dla API: ?format=json lub naglowek Accept: application/json -> JSON (kontrakt §6).
    """
    status, email = verify.confirm(token)
    logger.info("verify/confirm token=%s... -> %s", token[:8], status)

    accept = request.headers.get("accept", "")
    wants_json = format == "json" or "application/json" in accept

    if wants_json:
        if status == "invalid":
            return JSONResponse(status_code=400,
                                content={"detail": "Token niewazny lub wygasl"})
        return VerifyConfirmResponse(email=email, confirmed=True)

    # HTML dla przegladarki
    if status == "confirmed":
        return HTMLResponse(vtpl.page_confirmed(email))
    if status == "already":
        return HTMLResponse(vtpl.page_already(email))
    return HTMLResponse(vtpl.page_invalid(), status_code=400)


@app.get("/verify/status", response_model=VerifyStatusResponse)
def verify_status(email: str) -> VerifyStatusResponse:
    email = (email or "").strip().lower()
    return VerifyStatusResponse(
        email=email,
        confirmed=verify_store.is_confirmed(email),
        pending=verify_store.has_pending(email),
    )


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
        "smtp_dry_run": config.SMTP_DRY_RUN,
        "verify_store": verify_store.stats(),
        "verify_rate": {"max": config.VERIFY_RATE_MAX, "window_s": config.VERIFY_RATE_WINDOW_S},
        # Warstwy opcjonalne + ich runtime-przelaczniki (panel /admin).
        "toggles": runtime.states(),
        "crm": crm.stats(),
        "ai": ai_client.stats(),
        "smtp_check": smtp_check.stats(),
    }


# --- Panel administracyjny: przelaczniki warstw AI / CRM / SMTP ---------------
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
            "smtp": smtp_check.stats(),
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


@app.get("/", response_class=HTMLResponse)
def demo() -> str:
    demo_file = _STATIC / "demo.html"
    if demo_file.exists():
        return demo_file.read_text(encoding="utf-8")
    return "<h1>Walidator e-mail PoC</h1><p>Brak demo.html</p>"


# Statyki (widget.js itd.)
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
