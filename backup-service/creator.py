"""Render standby API called by QShield's synthetic demo or a future backend."""

import json
import logging
import os
import hashlib
import hmac
import html
import secrets
from urllib.parse import parse_qs

import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from crypto import StandbyCipher
from logic import TEST_ID, authenticate, canonical, request_digest, validate_handoff
from storage import ConflictError, NeonStore

app = FastAPI(title="QShield standby creator")
logger = logging.getLogger("uvicorn.error")


def demo_page(message="", request_id=None):
    """A small independent checkout for synthetic handoffs only."""
    request_id = request_id or "TEST-ORDER-" + secrets.token_hex(8)
    notice = f"<p class='notice'>{html.escape(message)}</p>" if message else ""
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>QShield backup demo</title><style>"
        "body{font:16px system-ui;background:#f5f7fb;color:#172033;margin:0;padding:32px}"
        "main{max-width:520px;background:white;margin:5vh auto;padding:32px;border-radius:16px;"
        "box-shadow:0 10px 32px #1421391a}h1{margin-top:0}label{display:block;margin:18px 0 6px}"
        "input{box-sizing:border-box;width:100%;padding:11px;border:1px solid #aeb8c7;border-radius:8px}"
        "button{margin-top:24px;padding:12px 18px;border:0;border-radius:8px;background:#2656d8;"
        "color:white;font-weight:700;cursor:pointer}.notice{padding:14px;background:#e7f2eb;"
        "border-radius:8px;overflow-wrap:anywhere}small{color:#526071}"
        "</style></head><body><main><h1>Backup payment demo</h1>"
        "<p>QShield is unavailable. This independent page records a <strong>fake</strong> "
        "payment handoff in Neon for Wallet review. No money moves.</p>"
        "<p><strong>Never enter card numbers, CVVs, or real customer details here.</strong></p>"
        + notice + "<form method='post' action='/demo'>"
        "<label for='request_id'>Test order ID</label><input id='request_id' name='request_id' "
        f"required pattern='TEST-[A-Za-z0-9_-]{{3,75}}' value='{html.escape(request_id, quote=True)}'>"
        "<label for='amount'>Fake amount in rupees</label><input id='amount' name='amount' "
        "type='number' min='1' max='1000000' step='1' required value='499'>"
        "<label for='passcode'>Demo access code</label><input id='passcode' name='passcode' "
        "type='password' required autocomplete='off'>"
        "<button type='submit'>Create fake handoff</button></form>"
        "<p><small>Reuse the same test order ID to check or retry it. The Wallet team "
        "accepts the request separately.</small></p></main></body></html>",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/demo", response_class=HTMLResponse)
def get_demo():
    return demo_page()


@app.post("/demo", response_class=HTMLResponse)
async def submit_demo(request: Request):
    body = await request.body()
    if len(body) > 1024 or request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(400, "Invalid demo form.")
    try:
        form = parse_qs(body.decode("utf-8"), strict_parsing=True)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, "Invalid demo form.") from exc
    if set(form) != {"request_id", "amount", "passcode"} or any(len(values) != 1 for values in form.values()):
        raise HTTPException(400, "Invalid demo form.")
    passcode = os.environ.get("BACKUP_DEMO_PASSCODE", "")
    if len(passcode) < 16 or not hmac.compare_digest(form["passcode"][0], passcode):
        raise HTTPException(403, "Invalid demo access code or missing server configuration.")
    request_id = form["request_id"][0]
    try:
        amount_rupees = int(form["amount"][0])
    except ValueError as exc:
        raise HTTPException(400, "Enter a whole-rupee fake amount.") from exc
    if not 1 <= amount_rupees <= 1_000_000:
        raise HTTPException(400, "Fake amount is out of range.")
    # Derive stable synthetic IDs: submitting one order twice reuses its Neon row.
    secret = os.environ.get("PRIMARY_TO_BACKUP_SECRET", "")
    if not secret:
        raise HTTPException(503, "Backup creator is not configured.")
    def stable(label):
        return hmac.new(secret.encode(), f"{label}:{request_id}".encode(), hashlib.sha256).hexdigest()
    payload = {
        "request_id": request_id,
        "token_id": "ptok_" + stable("token")[:32],
        "provider_reference": "mockpay_" + stable("provider")[:16],
        "amount_minor": amount_rupees * 100,
        "currency": "INR",
        "payment_status": "succeeded",
    }
    try:
        validate_handoff(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = save_handoff(payload)
    return demo_page(
        f"Test order {request_id}: Wallet status is {result['wallet_status']}. "
        "Your fake handoff is stored in Neon. Give this test order ID to the Wallet team.",
        request_id,
    )


def database_failure(exc):
    """Report a useful setup category without returning Neon credentials to clients."""
    logger.error("Backup database operation failed", exc_info=exc)
    if isinstance(exc, KeyError):
        detail = "BACKUP_DATABASE_URL is missing on the creator service."
    elif isinstance(exc, psycopg.errors.UndefinedTable):
        detail = "Neon table backup_handoffs is missing in this database."
    elif isinstance(exc, psycopg.OperationalError):
        detail = "Could not connect to Neon. Check BACKUP_DATABASE_URL on the creator service."
    else:
        detail = "Neon database operation failed. Check the creator runtime logs."
    raise HTTPException(503, detail) from exc


@app.get("/health")
def health():
    return {"status": "up"}


@app.get("/v1/handoffs/{request_id}")
def get_handoff(request_id: str, request: Request, x_timestamp: str = Header(default=""),
                x_signature: str = Header(default="")):
    if not authenticate(os.environ.get("PRIMARY_TO_BACKUP_SECRET"), x_timestamp, x_signature,
                        request.url.path, b""):
        raise HTTPException(401, "Invalid or expired backend signature.")
    if not TEST_ID.fullmatch(request_id):
        raise HTTPException(400, "Invalid test request ID.")
    try:
        data = NeonStore().get(request_id)
    except Exception as exc:
        database_failure(exc)
    if data is None:
        raise HTTPException(404, "Unknown handoff.")
    return {"request_id": request_id, "route": "neon_standby",
            "token_id": data["token_id"], "wallet_status": data["wallet_status"]}


@app.post("/v1/handoffs")
async def create_handoff(request: Request, x_timestamp: str = Header(default=""),
                         x_signature: str = Header(default="")):
    body = await request.body()
    if not authenticate(os.environ.get("PRIMARY_TO_BACKUP_SECRET"), x_timestamp, x_signature,
                        request.url.path, body):
        raise HTTPException(401, "Invalid or expired backend signature.")
    try:
        payload = validate_handoff(json.loads(body))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return save_handoff(payload)


def save_handoff(payload):
    """Store one test order once, regardless of which demo entry point sent it."""
    digest = request_digest(payload)
    try:
        store = NeonStore()
        prior = store.get(payload["request_id"])
    except Exception as exc:
        database_failure(exc)
    if prior is not None:
        if prior.get("request_digest") != digest:
            raise HTTPException(409, "Request ID already belongs to a different payment.")
        return {"request_id": payload["request_id"], "route": "neon_standby",
                "wallet_status": prior["wallet_status"], "created": False}

    try:
        cipher = StandbyCipher()
    except KeyError as exc:
        logger.error("Backup encryption key is missing", exc_info=exc)
        raise HTTPException(503, "BACKUP_AES_KEY_B64 is missing on the creator service.") from exc
    except ValueError as exc:
        logger.error("Backup encryption configuration failed", exc_info=exc)
        raise HTTPException(503, str(exc)) from exc
    envelope = cipher.encrypt(payload["request_id"], canonical(payload))
    record = {
        **envelope,
        "request_digest": digest,
        "token_id": payload["token_id"],
        "wallet_status": "pending",
        "route": "neon_standby",
    }
    try:
        store.create(record)
    except ConflictError:
        # A concurrent identical retry may have won the unique request ID.
        try:
            prior = store.get(payload["request_id"])
        except Exception as exc:
            database_failure(exc)
        if prior is None or prior.get("request_digest") != digest:
            raise HTTPException(409, "Request ID already belongs to a different payment.")
        return {"request_id": payload["request_id"], "route": "neon_standby",
                "wallet_status": prior["wallet_status"], "created": False}
    except Exception as exc:
        database_failure(exc)
    logger.info("Backup handoff created request_id=%s", payload["request_id"])
    return {"request_id": payload["request_id"], "route": "neon_standby",
            "wallet_status": "pending", "created": True}
