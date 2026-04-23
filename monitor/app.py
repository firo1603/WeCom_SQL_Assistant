"""
Corp Assistant Monitor — FastAPI main application.
"""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import tomllib

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import auth
from readers import artifacts as artifact_reader
from readers import sqlbot_health
from readers import state as state_reader
from readers import trace as trace_reader

# ─── config ──────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.toml"
ALIASES_PATH = BASE_DIR / "aliases.json"


def _load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def _save_secret_key(key: str) -> None:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    text = text.replace('secret_key = ""', f'secret_key = "{key}"')
    CONFIG_PATH.write_text(text, encoding="utf-8")


def _load_aliases() -> dict:
    if not ALIASES_PATH.exists():
        return {}
    try:
        return json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_aliases(aliases: dict) -> None:
    ALIASES_PATH.write_text(
        json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8"
    )


config = _load_config()

if not config["server"].get("secret_key"):
    sk = secrets.token_hex(32)
    config["server"]["secret_key"] = sk
    _save_secret_key(sk)

SECRET_KEY: str = config["server"]["secret_key"]

auth.ensure_default_admin()

# ─── app ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Corp Assistant Monitor", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _require_auth(request: Request) -> dict:
    user = auth.get_current_user(request, SECRET_KEY)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _get_events() -> list[dict]:
    return trace_reader.load_events(
        config["data"]["trace_file"],
        config["data"].get("tail_lines", 5000),
    )


# ─── auth pages ──────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if auth.get_current_user(request, SECRET_KEY):
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    user = auth.verify_password(username, password)
    if not user:
        return templates.TemplateResponse(
            request=request, name="login.html", context={"error": "用户名或密码错误"}
        )
    cookie_val = auth.make_session_cookie(SECRET_KEY, username)
    dest = "/change-password" if user.get("must_change_password") else "/"
    resp = RedirectResponse(url=dest, status_code=303)
    resp.set_cookie(
        auth.SESSION_COOKIE,
        cookie_val,
        httponly=True,
        max_age=auth.SESSION_MAX_AGE,
        samesite="lax",
    )
    return resp


@app.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    user = auth.get_current_user(request, SECRET_KEY)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(
        request=request,
        name="change_password.html",
        context={"error": None, "username": user["username"]},
    )


@app.post("/change-password")
async def change_password(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = auth.get_current_user(request, SECRET_KEY)
    if not user:
        return RedirectResponse(url="/login")
    if new_password != confirm_password:
        return templates.TemplateResponse(
            request=request, name="change_password.html",
            context={"username": user["username"], "error": "两次密码不一致"}
        )
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request=request, name="change_password.html",
            context={"username": user["username"], "error": "密码至少 8 位"}
        )
    auth.change_password(user["username"], new_password)
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


# ─── main SPA ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = auth.get_current_user(request, SECRET_KEY)
    if not user:
        return RedirectResponse(url="/login")
    if user.get("must_change_password"):
        return RedirectResponse(url="/change-password")
    return HTMLResponse((BASE_DIR / "static" / "index.html").read_text(encoding="utf-8"))


# ─── API ─────────────────────────────────────────────────────────────────────

@app.get("/api/overview")
async def api_overview(request: Request):
    _require_auth(request)
    return trace_reader.get_overview(_get_events())


@app.get("/api/traces")
async def api_traces(
    request: Request,
    status: str | None = None,
    session: str | None = None,
    date: str | None = None,
):
    _require_auth(request)
    filters = {k: v for k, v in {"status": status, "session": session, "date": date}.items() if v}
    return trace_reader.get_trace_list(_get_events(), filters or None)


@app.get("/api/traces/{trace_id}")
async def api_trace_detail(request: Request, trace_id: str):
    _require_auth(request)
    detail = trace_reader.get_trace_detail(_get_events(), trace_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Trace not found")
    return detail


@app.get("/api/sessions")
async def api_sessions(request: Request):
    _require_auth(request)
    return state_reader.get_sessions(config["data"]["state_file"])


@app.get("/api/sqlbot/health")
async def api_sqlbot_health(request: Request):
    _require_auth(request)
    return await sqlbot_health.check_sqlbot_health(
        config["data"]["skill_env"],
        config["sqlbot"].get("health_timeout", 5),
    )


@app.get("/api/artifacts")
async def api_artifacts(request: Request, trace_id: str | None = None):
    _require_auth(request)
    return artifact_reader.get_artifact_summary(config["data"]["artifacts_dir"], trace_id)


@app.get("/api/aliases")
async def api_aliases_get(request: Request):
    _require_auth(request)
    return _load_aliases()


@app.post("/api/aliases")
async def api_aliases_set(request: Request):
    _require_auth(request)
    body = await request.json()
    key = (body.get("key") or "").strip()
    name = (body.get("name") or "").strip()
    if not key or not name:
        raise HTTPException(status_code=400, detail="key and name required")
    aliases = _load_aliases()
    aliases[key] = name
    _save_aliases(aliases)
    return aliases


@app.delete("/api/aliases/{key}")
async def api_aliases_delete(request: Request, key: str):
    _require_auth(request)
    aliases = _load_aliases()
    aliases.pop(key, None)
    _save_aliases(aliases)
    return aliases


@app.get("/api/conversations")
async def api_conversations(
    request: Request,
    q: str | None = None,
    user: str | None = None,
    status: str | None = None,
    date: str | None = None,
    datasource: str | None = None,
):
    _require_auth(request)
    aliases = _load_aliases()
    filters = {k: v for k, v in {"q": q, "user": user, "status": status, "date": date, "datasource": datasource}.items() if v}
    return artifact_reader.get_conversations(
        config["data"]["artifacts_dir"],
        aliases=aliases,
        filters=filters,
    )


# ─── entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=config["server"]["host"],
        port=config["server"]["port"],
    )
