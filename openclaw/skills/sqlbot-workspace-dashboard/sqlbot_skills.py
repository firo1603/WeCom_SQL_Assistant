#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener

API_PREFIX = "/api/v1"
DEFAULT_ENV_FILE = ".env"
DEFAULT_STATE_FILE = ".sqlbot-skill-state.json"
DEFAULT_DASHBOARD_EXPORT_FORMAT = "jpg"
DEFAULT_PREVIEW_ROWS = 8
DEFAULT_TABLE_ROWS = 20
DEFAULT_TOP_N = 15
SCREENSHOT_EXPORT_FORMATS = {"jpg", "jpeg", "png"}


class SQLBotSkillError(RuntimeError):
    pass


class ConfigError(SQLBotSkillError):
    pass


class APIError(SQLBotSkillError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class BrowserError(SQLBotSkillError):
    pass


def normalize_export_format(export_format: str) -> str:
    normalized = export_format.lower()
    if normalized == "jpeg":
        return "jpg"
    if normalized not in SCREENSHOT_EXPORT_FORMATS | {"pdf"}:
        raise BrowserError("Unsupported export format. Use `jpg`, `png` or `pdf`.")
    return normalized


@dataclass(frozen=True)
class Workspace:
    id: int
    name: str

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Workspace":
        return cls(id=int(payload["id"]), name=str(payload["name"]))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True)
class Datasource:
    id: int
    name: str
    description: str | None = None
    type: str | None = None
    type_name: str | None = None
    num: int | None = None
    status: int | None = None
    oid: int | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Datasource":
        return cls(
            id=int(payload["id"]),
            name=str(payload["name"]),
            description=_coalesce(payload.get("description")),
            type=_coalesce(payload.get("type")),
            type_name=_coalesce(payload.get("type_name")),
            num=_maybe_int(payload.get("num")),
            status=_maybe_int(payload.get("status")),
            oid=_maybe_int(payload.get("oid")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "type": self.type,
            "type_name": self.type_name,
            "num": self.num,
            "status": self.status,
            "oid": self.oid,
        }


@dataclass
class DashboardNode:
    id: str | None = None
    name: str | None = None
    pid: str | None = None
    node_type: str | None = None
    leaf: bool = False
    type: str | None = None
    create_time: int | None = None
    update_time: int | None = None
    children: list["DashboardNode"] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "DashboardNode":
        return cls(
            id=_coalesce(payload.get("id")),
            name=_coalesce(payload.get("name")),
            pid=_coalesce(payload.get("pid")),
            node_type=_coalesce(payload.get("node_type")),
            leaf=bool(payload.get("leaf", False)),
            type=_coalesce(payload.get("type")),
            create_time=_maybe_int(payload.get("create_time")),
            update_time=_maybe_int(payload.get("update_time")),
            children=[cls.from_api(item) for item in payload.get("children", []) if isinstance(item, dict)],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "pid": self.pid,
            "node_type": self.node_type,
            "leaf": self.leaf,
            "type": self.type,
            "create_time": self.create_time,
            "update_time": self.update_time,
            "children": [item.to_dict() for item in self.children],
        }

    def walk(self) -> list["DashboardNode"]:
        flattened = [self]
        for child in self.children:
            flattened.extend(child.walk())
        return flattened


@dataclass(frozen=True)
class SkillSettings:
    base_url: str
    access_key: str
    secret_key: str
    browser_path: str | None
    state_file: str
    default_workspace: str | None
    default_datasource: str | None
    timeout: float
    api_key_ttl_seconds: int
    env_file: str | None

    @classmethod
    def load(
        cls,
        *,
        env_file: str | None = None,
        base_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        browser_path: str | None = None,
        state_file: str | None = None,
        timeout: float | None = None,
        api_key_ttl_seconds: int | None = None,
    ) -> "SkillSettings":
        env_values = load_env_file(env_file)
        resolved_base_url = _coalesce(base_url, env_values.get("SQLBOT_BASE_URL"))
        resolved_access_key = _coalesce(access_key, env_values.get("SQLBOT_API_KEY_ACCESS_KEY"))
        resolved_secret_key = _coalesce(secret_key, env_values.get("SQLBOT_API_KEY_SECRET_KEY"))
        resolved_browser_path = _coalesce(browser_path, env_values.get("SQLBOT_BROWSER_PATH"))
        resolved_state_file = _resolve_state_path(state_file, env_values.get("SQLBOT_STATE_FILE"))
        resolved_default_workspace = _coalesce(env_values.get("SQLBOT_DEFAULT_WORKSPACE"))
        resolved_default_datasource = _coalesce(env_values.get("SQLBOT_DEFAULT_DATASOURCE"))
        resolved_timeout = _parse_float(timeout, env_values.get("SQLBOT_TIMEOUT"), default=30.0, label="SQLBOT_TIMEOUT")
        resolved_ttl = _parse_int(
            api_key_ttl_seconds,
            env_values.get("SQLBOT_API_KEY_TTL_SECONDS"),
            default=300,
            label="SQLBOT_API_KEY_TTL_SECONDS",
        )

        if not resolved_base_url:
            raise ConfigError("Missing SQLBOT_BASE_URL in .env or command arguments.")
        if not resolved_access_key:
            raise ConfigError("Missing SQLBOT_API_KEY_ACCESS_KEY in .env or command arguments.")
        if not resolved_secret_key:
            raise ConfigError("Missing SQLBOT_API_KEY_SECRET_KEY in .env or command arguments.")

        resolved_env_path = _resolve_env_path(env_file)
        return cls(
            base_url=resolved_base_url,
            access_key=resolved_access_key,
            secret_key=resolved_secret_key,
            browser_path=resolved_browser_path,
            state_file=str(resolved_state_file),
            default_workspace=resolved_default_workspace,
            default_datasource=resolved_default_datasource,
            timeout=resolved_timeout,
            api_key_ttl_seconds=resolved_ttl,
            env_file=str(resolved_env_path) if resolved_env_path else None,
        )


@dataclass(frozen=True)
class OpenClawContext:
    scope_id: str
    session_key: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    message_channel: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "session_key": self.session_key,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "message_channel": self.message_channel,
        }


@dataclass
class SessionState:
    scope_id: str
    current_workspace: Workspace | None = None
    current_datasource: Datasource | None = None
    sqlbot_chat_id: int | None = None
    last_record_id: int | None = None
    last_question: str | None = None
    last_sql: str | None = None
    last_chart_kind: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    session_key: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    status: str = "active"

    @classmethod
    def new(cls, context: OpenClawContext) -> "SessionState":
        now = _now_iso()
        return cls(
            scope_id=context.scope_id,
            session_key=context.session_key,
            session_id=context.session_id,
            agent_id=context.agent_id,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def from_dict(cls, scope_id: str, payload: dict[str, Any]) -> "SessionState":
        workspace_payload = payload.get("current_workspace")
        datasource_payload = payload.get("current_datasource")
        return cls(
            scope_id=scope_id,
            current_workspace=Workspace.from_api(workspace_payload) if isinstance(workspace_payload, dict) else None,
            current_datasource=Datasource.from_api(datasource_payload) if isinstance(datasource_payload, dict) else None,
            sqlbot_chat_id=_maybe_int(payload.get("sqlbot_chat_id")),
            last_record_id=_maybe_int(payload.get("last_record_id")),
            last_question=_coalesce(payload.get("last_question")),
            last_sql=_coalesce(payload.get("last_sql")),
            last_chart_kind=_coalesce(payload.get("last_chart_kind")),
            artifacts={str(key): str(value) for key, value in payload.get("artifacts", {}).items()}
            if isinstance(payload.get("artifacts"), dict)
            else {},
            session_key=_coalesce(payload.get("session_key")),
            session_id=_coalesce(payload.get("session_id")),
            agent_id=_coalesce(payload.get("agent_id")),
            created_at=_coalesce(payload.get("created_at")),
            updated_at=_coalesce(payload.get("updated_at")),
            status=_coalesce(payload.get("status")) or "active",
        )

    def bind_context(self, context: OpenClawContext) -> None:
        if context.session_key:
            self.session_key = context.session_key
        if context.session_id:
            self.session_id = context.session_id
        if context.agent_id:
            self.agent_id = context.agent_id
        if not self.created_at:
            self.created_at = _now_iso()
        self.updated_at = _now_iso()

    def clear_chat(self, *, full: bool = False) -> None:
        self.sqlbot_chat_id = None
        self.last_record_id = None
        self.last_question = None
        self.last_sql = None
        self.last_chart_kind = None
        self.artifacts = {}
        if full:
            self.current_workspace = None
            self.current_datasource = None
        self.updated_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "current_workspace": self.current_workspace.to_dict() if self.current_workspace else None,
            "current_datasource": self.current_datasource.to_dict() if self.current_datasource else None,
            "sqlbot_chat_id": self.sqlbot_chat_id,
            "last_record_id": self.last_record_id,
            "last_question": self.last_question,
            "last_sql": self.last_sql,
            "last_chart_kind": self.last_chart_kind,
            "artifacts": self.artifacts,
            "session_key": self.session_key,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "current_workspace": self.current_workspace.to_dict() if self.current_workspace else None,
            "current_datasource": self.current_datasource.to_dict() if self.current_datasource else None,
            "sqlbot_chat_id": self.sqlbot_chat_id,
            "last_record_id": self.last_record_id,
            "last_question": self.last_question,
            "last_chart_kind": self.last_chart_kind,
            "artifacts": self.artifacts,
            "session_key": self.session_key,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "updated_at": self.updated_at,
            "status": self.status,
        }


@dataclass
class SkillStateStore:
    version: int = 1
    sessions: dict[str, SessionState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "SkillStateStore":
        state_path = Path(path)
        if not state_path.exists():
            return cls()
        decoded = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(decoded, dict) and "sessions" in decoded:
            raw_sessions = decoded.get("sessions")
            sessions = {}
            if isinstance(raw_sessions, dict):
                for scope_id, payload in raw_sessions.items():
                    if isinstance(payload, dict):
                        sessions[str(scope_id)] = SessionState.from_dict(str(scope_id), payload)
            return cls(version=_maybe_int(decoded.get("version")) or 1, sessions=sessions)
        if isinstance(decoded, dict):
            # Legacy single-scope state file.
            legacy_scope = "legacy:default"
            session = SessionState.from_dict(legacy_scope, decoded)
            return cls(version=1, sessions={legacy_scope: session})
        return cls()

    def save(self, path: str | Path) -> None:
        state_path = Path(path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "sessions": {scope_id: session.to_dict() for scope_id, session in sorted(self.sessions.items())},
        }
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def ensure_session(self, context: OpenClawContext) -> SessionState:
        session = self.sessions.get(context.scope_id)
        if session is None:
            session = SessionState.new(context)
            self.sessions[context.scope_id] = session
        else:
            session.bind_context(context)
        return session


def load_env_file(env_file: str | None = None) -> dict[str, str]:
    path = _resolve_env_path(env_file)
    if path is None:
        return {}
    if not path.exists():
        if env_file:
            raise ConfigError(f".env file not found: {path}")
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def normalize_api_base_url(base_url: str) -> str:
    cleaned = str(base_url).strip().rstrip("/")
    if not cleaned:
        raise ConfigError("SQLBot base URL is required.")
    if cleaned.endswith(API_PREFIX):
        return cleaned
    return f"{cleaned}{API_PREFIX}"


def derive_app_url(base_url: str) -> str:
    normalized = normalize_api_base_url(base_url)
    return normalized[: -len(API_PREFIX)] or normalized


def build_api_key_header(*, access_key: str, secret_key: str, ttl_seconds: int = 300, now: int | None = None) -> str:
    if not access_key:
        raise ConfigError("SQLBot API key access_key is required.")
    if not secret_key:
        raise ConfigError("SQLBot API key secret_key is required.")
    if ttl_seconds <= 0:
        raise ConfigError("SQLBot API key ttl_seconds must be greater than 0.")

    issued_at = int(time.time() if now is None else now)
    payload = {"access_key": access_key, "iat": issued_at, "exp": issued_at + int(ttl_seconds)}
    return f"sk {_encode_jwt(payload, secret_key)}"


def _encode_jwt(payload: dict[str, Any], secret_key: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_segment = _base64url_json(header)
    payload_segment = _base64url_json(payload)
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_segment}.{payload_segment}.{_base64url_encode(signature)}"


def _base64url_json(value: dict[str, Any]) -> str:
    return _base64url_encode(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _resolve_env_path(env_file: str | None) -> Path | None:
    if env_file:
        return Path(env_file).expanduser()
    skill_path = Path(__file__).resolve().with_name(DEFAULT_ENV_FILE)
    if skill_path.exists():
        return skill_path
    cwd_path = Path.cwd() / DEFAULT_ENV_FILE
    if cwd_path.exists():
        return cwd_path
    return None


def _resolve_state_path(state_file: str | None, env_state_file: str | None = None) -> Path:
    if state_file:
        return Path(state_file).expanduser()
    if env_state_file:
        return Path(env_state_file).expanduser()
    return Path(__file__).resolve().with_name(DEFAULT_STATE_FILE)


def _coalesce(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(direct_value: float | None, env_value: str | None, *, default: float, label: str) -> float:
    if direct_value is not None:
        return float(direct_value)
    text = _coalesce(env_value)
    if text is None:
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise ConfigError(f"{label} must be a number.") from exc


def _parse_int(direct_value: int | None, env_value: str | None, *, default: int, label: str) -> int:
    if direct_value is not None:
        return int(direct_value)
    text = _coalesce(env_value)
    if text is None:
        return default
    try:
        return int(text)
    except ValueError as exc:
        raise ConfigError(f"{label} must be an integer.") from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _resolve_openclaw_state_root() -> Path:
    root = os.environ.get("OPENCLAW_STATE_DIR")
    if root:
        return Path(root).expanduser()
    return Path.home() / ".openclaw"


def _parse_agent_id_from_session_key(session_key: str | None) -> str | None:
    text = _coalesce(session_key)
    if not text:
        return None
    parts = text.split(":")
    if len(parts) >= 2 and parts[0] == "agent":
        return parts[1]
    return None


def _resolve_openclaw_session_id(*, state_root: Path, agent_id: str | None, session_key: str | None) -> str | None:
    normalized_agent = _coalesce(agent_id)
    normalized_key = _coalesce(session_key)
    if not normalized_agent or not normalized_key:
        return None
    metadata_path = state_root / "agents" / normalized_agent / "sessions" / "sessions.json"
    if not metadata_path.exists():
        return None
    try:
        decoded = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    entry = decoded.get(normalized_key)
    if isinstance(entry, dict):
        return _coalesce(entry.get("sessionId"))
    return None


def resolve_openclaw_context(
    *,
    session_key: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> OpenClawContext:
    resolved_session_key = _coalesce(session_key, os.environ.get("OPENCLAW_MCP_SESSION_KEY"))
    resolved_agent_id = _coalesce(
        agent_id,
        os.environ.get("OPENCLAW_MCP_AGENT_ID"),
        _parse_agent_id_from_session_key(resolved_session_key),
    )
    state_root = _resolve_openclaw_state_root()
    resolved_session_id = _coalesce(session_id, os.environ.get("OPENCLAW_MCP_SESSION_ID"))
    if resolved_session_id is None and resolved_session_key:
        resolved_session_id = _resolve_openclaw_session_id(
            state_root=state_root,
            agent_id=resolved_agent_id,
            session_key=resolved_session_key,
        )
    scope_id = (
        resolved_session_id
        or (f"key:{resolved_session_key}" if resolved_session_key else None)
        or "default"
    )
    return OpenClawContext(
        scope_id=scope_id,
        session_key=resolved_session_key,
        session_id=resolved_session_id,
        agent_id=resolved_agent_id,
        message_channel=_coalesce(os.environ.get("OPENCLAW_MCP_MESSAGE_CHANNEL")),
    )


def _slugify(value: str, *, fallback: str = "item", limit: int = 80) -> str:
    pieces: list[str] = []
    for char in str(value).strip():
        if char.isalnum() or char in {"-", "_"}:
            pieces.append(char)
        else:
            pieces.append("-")
    slug = "".join(pieces).strip("-_")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug[:limit].strip("-_") or fallback)


def _parse_chart_payload(raw_chart: Any) -> dict[str, Any] | None:
    if isinstance(raw_chart, dict):
        return raw_chart
    text = _coalesce(raw_chart)
    if text is None:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _normalize_data_payload(raw_data: Any) -> tuple[list[str], list[dict[str, Any]]]:
    if isinstance(raw_data, dict):
        fields = raw_data.get("fields")
        rows = raw_data.get("data")
        if isinstance(fields, list) and isinstance(rows, list):
            normalized_rows = [item for item in rows if isinstance(item, dict)]
            return [str(item) for item in fields], normalized_rows
    if isinstance(raw_data, list):
        normalized_rows = [item for item in raw_data if isinstance(item, dict)]
        if normalized_rows:
            return [str(key) for key in normalized_rows[0].keys()], normalized_rows
    return [], []


def _parse_numeric(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text.lower() == "null":
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _format_metric(value: Any) -> str:
    number = _parse_numeric(value)
    if number is None:
        return str(value)
    if float(number).is_integer():
        return str(int(number))
    return f"{number:.2f}"


def _looks_temporal_field(field: str, rows: list[dict[str, Any]]) -> bool:
    lowered = field.casefold()
    if any(token in lowered for token in ("date", "time", "month", "day", "日期", "时间", "月份", "周")):
        return True
    samples = [str(row.get(field, "")).strip() for row in rows[:5]]
    return any("-" in sample or "/" in sample for sample in samples if sample)


def _pick_category_field(fields: list[str], rows: list[dict[str, Any]]) -> str | None:
    for field in fields:
        values = [row.get(field) for row in rows[:10]]
        if any(_parse_numeric(value) is None for value in values if value not in (None, "")):
            return field
    return fields[0] if fields else None


def _pick_numeric_fields(fields: list[str], rows: list[dict[str, Any]]) -> list[str]:
    candidates: list[str] = []
    for field in fields:
        samples = [row.get(field) for row in rows[:20]]
        numeric_samples = [_parse_numeric(sample) for sample in samples]
        present = [sample for sample in samples if sample not in (None, "", "null", "NULL")]
        if present and all(number is not None for number in numeric_samples[: len(present)]):
            candidates.append(field)
    return candidates


def _choose_chart_plan(
    *,
    fields: list[str],
    rows: list[dict[str, Any]],
    chart_payload: dict[str, Any] | None,
    top_n: int,
) -> dict[str, Any]:
    title = _coalesce(chart_payload.get("title") if isinstance(chart_payload, dict) else None) or "SQLBot 问数结果"
    if not rows:
        return {"kind": "empty", "title": title, "fields": fields, "row_count": 0}

    chart_type = _coalesce(chart_payload.get("type") if isinstance(chart_payload, dict) else None)
    if chart_type == "table":
        preview_rows = rows[: min(DEFAULT_TABLE_ROWS, len(rows))]
        return {"kind": "table", "title": title, "fields": fields, "rows": preview_rows, "row_count": len(rows)}

    axis = chart_payload.get("axis") if isinstance(chart_payload, dict) else None
    x_field = None
    metric_fields: list[str] = []
    if isinstance(axis, dict):
        x_axis = axis.get("x")
        if isinstance(x_axis, dict):
            x_field = _coalesce(x_axis.get("value"))
        y_axis = axis.get("y")
        if isinstance(y_axis, list):
            for item in y_axis:
                if isinstance(item, dict):
                    candidate = _coalesce(item.get("value"))
                    if candidate and candidate in fields and candidate not in metric_fields:
                        metric_fields.append(candidate)
        series_axis = axis.get("series")
        if isinstance(series_axis, dict):
            candidate = _coalesce(series_axis.get("value"))
            if candidate and candidate in fields and candidate not in metric_fields:
                metric_fields.append(candidate)

    if x_field not in fields:
        x_field = _pick_category_field(fields, rows)
    if not metric_fields:
        metric_fields = _pick_numeric_fields(fields, rows)[:2]

    if not x_field or not metric_fields:
        preview_rows = rows[: min(DEFAULT_TABLE_ROWS, len(rows))]
        return {"kind": "table", "title": title, "fields": fields, "rows": preview_rows, "row_count": len(rows)}

    chart_kind = "line" if chart_type == "line" or _looks_temporal_field(x_field, rows) else "bar"
    ranked_rows = list(rows)
    if chart_kind == "bar":
        ranked_rows.sort(key=lambda row: _parse_numeric(row.get(metric_fields[0])) or float("-inf"), reverse=True)
    ranked_rows = ranked_rows[: min(top_n, len(ranked_rows))]
    return {
        "kind": chart_kind,
        "title": title,
        "fields": fields,
        "rows": ranked_rows,
        "row_count": len(rows),
        "category_field": x_field,
        "value_fields": metric_fields[:2],
    }


def _build_summary_lines(
    *,
    fields: list[str],
    rows: list[dict[str, Any]],
    chart_plan: dict[str, Any],
) -> list[str]:
    lines = [f"共返回 {len(rows)} 行，字段包括：{', '.join(fields[:5])}。"]
    category_field = chart_plan.get("category_field")
    value_fields = chart_plan.get("value_fields") or []
    if category_field and value_fields:
        metric = value_fields[0]
        ranked = [row for row in rows if _parse_numeric(row.get(metric)) is not None]
        if ranked:
            top_row = max(ranked, key=lambda row: _parse_numeric(row.get(metric)) or float("-inf"))
            lines.append(f"{top_row.get(category_field)} 的 {metric} 最高，为 {_format_metric(top_row.get(metric))}。")
        if len(value_fields) > 1 and ranked:
            second_metric = value_fields[1]
            second_ranked = [row for row in rows if _parse_numeric(row.get(second_metric)) is not None]
            if second_ranked:
                top_second = max(second_ranked, key=lambda row: _parse_numeric(row.get(second_metric)) or float("-inf"))
                lines.append(
                    f"{top_second.get(category_field)} 的 {second_metric} 最高，为 {_format_metric(top_second.get(second_metric))}。"
                )
    return lines


def _compact_text(value: Any, *, limit: int = 60) -> str | None:
    text = _coalesce(value)
    if text is None:
        return None
    normalized = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 1)]}…"


def _summarize_execution_error(raw_error: Any) -> str:
    text = _compact_text(raw_error, limit=80)
    if text is None:
        return "查询执行失败"
    lowered = text.casefold()
    if "invalid api key" in lowered or "authentication" in lowered or "unauthorized" in lowered or "401" in lowered:
        return "认证失败"
    if "workspace not found" in lowered:
        return "工作空间不存在"
    if "datasource not found" in lowered:
        return "数据源不存在"
    if "resource not found" in lowered or "资源未找到" in text or "未找到资源" in text:
        return "未找到可用的数据资源"
    if "permission" in lowered or "forbidden" in lowered or "权限" in text:
        return "权限不足"
    if "timeout" in lowered or "timed out" in lowered or "超时" in text:
        return "请求超时"
    if "failed to reach sqlbot" in lowered or "connection" in lowered or "连接" in text:
        return "无法连接到问数服务"
    if "sql" in lowered and ("error" in lowered or "失败" in text):
        return "SQL 执行失败"
    return text


def _build_ask_outcome(
    *,
    result: dict[str, Any],
    fields: list[str],
    rows: list[dict[str, Any]],
    chart_plan: dict[str, Any],
) -> dict[str, Any]:
    raw_error = _coalesce(result.get("error"))
    if raw_error is not None:
        error_reason = _summarize_execution_error(raw_error)
        return {
            "status": "error",
            "error_reason": error_reason,
            "user_hint": "请直接补充指标、对象、时间范围或筛选条件后重新提问。",
            "summary_lines": [
                f"SQLBot 执行失败：{error_reason}。",
                "请直接补充指标、对象、时间范围或筛选条件后重新提问。",
            ],
        }
    if not rows:
        if not result.get("finished") and result.get("record_id") is None:
            return {
                "status": "error",
                "error_reason": "未收到有效查询结果",
                "user_hint": "请直接补充指标、对象、时间范围或筛选条件后重新提问。",
                "summary_lines": [
                    "SQLBot 执行失败：未收到有效查询结果。",
                    "请直接补充指标、对象、时间范围或筛选条件后重新提问。",
                ],
            }
        return {
            "status": "empty",
            "error_reason": None,
            "user_hint": "请调整时间范围、指标名称、查询对象或筛选条件后重试。",
            "summary_lines": [
                "查询已执行成功，但没有返回符合条件的数据。",
                "请调整时间范围、指标名称、查询对象或筛选条件后重试。",
            ],
        }
    return {
        "status": "ok",
        "error_reason": None,
        "user_hint": None,
        "summary_lines": _build_summary_lines(fields=fields, rows=rows, chart_plan=chart_plan),
    }


def _write_csv(path: Path, *, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def _load_pillow_fonts() -> tuple[Any, Any, Any]:
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return (
                ImageFont.truetype(candidate, 26),
                ImageFont.truetype(candidate, 18),
                ImageFont.truetype(candidate, 16),
            )
    default = ImageFont.load_default()
    return default, default, default


def _trim_text(text: Any, limit: int = 36) -> str:
    rendered = str(text)
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: max(0, limit - 1)]}…"


def _text_width(draw: Any, text: str, font: Any) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return max(0, bbox[2] - bbox[0])


def _render_table_png(path: Path, *, fields: list[str], rows: list[dict[str, Any]], title: str) -> None:
    from PIL import Image, ImageDraw

    title_font, header_font, body_font = _load_pillow_fonts()
    table_rows = rows[: min(DEFAULT_TABLE_ROWS, len(rows))]
    margin = 36
    header_height = 44
    row_height = 34
    title_height = 54
    footer_height = 28
    image_width = 1600
    image_height = title_height + header_height + len(table_rows) * row_height + footer_height + margin * 2
    image = Image.new("RGB", (image_width, image_height), color=(250, 251, 252))
    draw = ImageDraw.Draw(image)

    draw.text((margin, margin), title, font=title_font, fill=(28, 37, 65))
    top = margin + title_height
    draw.rectangle([margin, top, image_width - margin, top + header_height], fill=(31, 64, 104))

    column_width = max(120, (image_width - margin * 2) // max(1, len(fields)))
    x = margin
    for field in fields:
        draw.text((x + 8, top + 11), _trim_text(field, 18), font=header_font, fill=(255, 255, 255))
        x += column_width

    y = top + header_height
    for index, row in enumerate(table_rows):
        background = (255, 255, 255) if index % 2 == 0 else (242, 245, 248)
        draw.rectangle([margin, y, image_width - margin, y + row_height], fill=background)
        x = margin
        for field in fields:
            draw.text((x + 8, y + 8), _trim_text(row.get(field, ""), 22), font=body_font, fill=(32, 40, 54))
            x += column_width
        y += row_height

    draw.text((margin, image_height - margin), f"展示前 {len(table_rows)} 行，共 {len(rows)} 行", font=body_font, fill=(95, 104, 118))
    image.save(path)


def _render_bar_png(
    path: Path,
    *,
    category_field: str,
    value_fields: list[str],
    rows: list[dict[str, Any]],
    title: str,
) -> None:
    from PIL import Image, ImageDraw

    title_font, header_font, body_font = _load_pillow_fonts()
    labels = [_trim_text(row.get(category_field, ""), 24) for row in rows]
    values_primary = [_parse_numeric(row.get(value_fields[0])) or 0.0 for row in rows]
    values_secondary = (
        [_parse_numeric(row.get(value_fields[1])) or 0.0 for row in rows]
        if len(value_fields) > 1
        else []
    )
    max_value = max(values_primary + values_secondary + [1.0])
    margin = 40
    title_height = 56
    row_height = 42 if values_secondary else 34
    image_width = 1600
    image_height = title_height + len(labels) * row_height + margin * 3
    image = Image.new("RGB", (image_width, image_height), color=(250, 251, 252))
    draw = ImageDraw.Draw(image)

    draw.text((margin, margin), title, font=title_font, fill=(28, 37, 65))
    plot_left = 420
    plot_right = image_width - margin
    chart_width = max(1, plot_right - plot_left)
    y = margin + title_height
    for index, label in enumerate(labels):
        draw.text((margin, y), label, font=body_font, fill=(32, 40, 54))
        primary_width = int(chart_width * ((values_primary[index]) / max_value))
        draw.rounded_rectangle([plot_left, y + 4, plot_left + primary_width, y + 16], radius=5, fill=(42, 111, 151))
        draw.text((plot_left + primary_width + 8, y), _format_metric(values_primary[index]), font=body_font, fill=(42, 111, 151))
        if values_secondary:
            secondary_width = int(chart_width * ((values_secondary[index]) / max_value))
            draw.rounded_rectangle([plot_left, y + 22, plot_left + secondary_width, y + 34], radius=5, fill=(231, 111, 81))
            draw.text(
                (plot_left + secondary_width + 8, y + 18),
                _format_metric(values_secondary[index]),
                font=body_font,
                fill=(231, 111, 81),
            )
        y += row_height

    legend_y = image_height - margin - 24
    draw.rectangle([margin, legend_y, margin + 16, legend_y + 10], fill=(42, 111, 151))
    draw.text((margin + 24, legend_y - 4), value_fields[0], font=body_font, fill=(32, 40, 54))
    if values_secondary:
        draw.rectangle([margin + 220, legend_y, margin + 236, legend_y + 10], fill=(231, 111, 81))
        draw.text((margin + 244, legend_y - 4), value_fields[1], font=body_font, fill=(32, 40, 54))
    image.save(path)


def _render_line_png(
    path: Path,
    *,
    category_field: str,
    value_fields: list[str],
    rows: list[dict[str, Any]],
    title: str,
) -> None:
    from PIL import Image, ImageDraw

    title_font, header_font, body_font = _load_pillow_fonts()
    labels = [_trim_text(row.get(category_field, ""), 14) for row in rows]
    series = [[_parse_numeric(row.get(field)) or 0.0 for row in rows] for field in value_fields]
    max_value = max([value for line in series for value in line] + [1.0])
    margin = 56
    image_width = 1600
    image_height = 900
    plot_left = 120
    plot_top = 120
    plot_right = image_width - 80
    plot_bottom = image_height - 180
    chart_width = max(1, plot_right - plot_left)
    chart_height = max(1, plot_bottom - plot_top)
    image = Image.new("RGB", (image_width, image_height), color=(250, 251, 252))
    draw = ImageDraw.Draw(image)

    draw.text((margin, 32), title, font=title_font, fill=(28, 37, 65))
    draw.line([plot_left, plot_top, plot_left, plot_bottom], fill=(120, 130, 146), width=2)
    draw.line([plot_left, plot_bottom, plot_right, plot_bottom], fill=(120, 130, 146), width=2)

    palette = [(42, 111, 151), (231, 111, 81)]
    count = max(1, len(rows) - 1)
    for series_index, field in enumerate(value_fields):
        points: list[tuple[int, int]] = []
        for index, value in enumerate(series[series_index]):
            x = plot_left + int(chart_width * (index / count))
            y = plot_bottom - int(chart_height * (value / max_value))
            points.append((x, y))
        if len(points) > 1:
            draw.line(points, fill=palette[series_index % len(palette)], width=4)
        for point in points:
            draw.ellipse([point[0] - 5, point[1] - 5, point[0] + 5, point[1] + 5], fill=palette[series_index % len(palette)])
        draw.text((plot_right - 220, 30 + series_index * 28), field, font=header_font, fill=palette[series_index % len(palette)])

    x_step = max(1, len(labels) // 10)
    for index, label in enumerate(labels):
        if index % x_step != 0 and index != len(labels) - 1:
            continue
        x = plot_left + int(chart_width * (index / count))
        draw.text((x - 10, plot_bottom + 14), label, font=body_font, fill=(32, 40, 54))
    image.save(path)


def _render_chart_artifact(path: Path, *, chart_plan: dict[str, Any]) -> None:
    kind = chart_plan.get("kind")
    if kind == "table":
        _render_table_png(path, fields=chart_plan["fields"], rows=chart_plan["rows"], title=chart_plan["title"])
        return
    if kind == "line":
        _render_line_png(
            path,
            category_field=chart_plan["category_field"],
            value_fields=chart_plan["value_fields"],
            rows=chart_plan["rows"],
            title=chart_plan["title"],
        )
        return
    _render_bar_png(
        path,
        category_field=chart_plan["category_field"],
        value_fields=chart_plan["value_fields"],
        rows=chart_plan["rows"],
        title=chart_plan["title"],
    )


class SQLBotClient:
    def __init__(
        self,
        *,
        base_url: str,
        access_key: str,
        secret_key: str,
        api_key_ttl_seconds: int = 300,
        timeout: float = 30.0,
    ) -> None:
        self.api_base_url = normalize_api_base_url(base_url)
        self.access_key = access_key
        self.secret_key = secret_key
        self.api_key_ttl_seconds = int(api_key_ttl_seconds)
        self.timeout = timeout
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def build_auth_headers(self) -> dict[str, str]:
        return {
            "X-SQLBOT-ASK-TOKEN": build_api_key_header(
                access_key=self.access_key,
                secret_key=self.secret_key,
                ttl_seconds=self.api_key_ttl_seconds,
            )
        }

    def list_workspaces(self) -> list[Workspace]:
        payload = self._request("GET", "/user/ws")
        return [Workspace.from_api(item) for item in payload]

    def resolve_workspace(self, workspace: int | str | Workspace) -> Workspace:
        if isinstance(workspace, Workspace):
            return workspace
        workspaces = self.list_workspaces()
        text_ref = str(workspace).strip()
        if not text_ref:
            raise ConfigError("Workspace reference cannot be empty.")
        if text_ref.isdigit():
            workspace_id = int(text_ref)
            for item in workspaces:
                if item.id == workspace_id:
                    return item
        lowered = text_ref.casefold()
        for item in workspaces:
            if item.name == text_ref or item.name.casefold() == lowered:
                return item
        raise APIError(f"Workspace not found: {workspace}")

    def switch_workspace(self, workspace: int | str | Workspace) -> Workspace:
        resolved = self.resolve_workspace(workspace)
        self._request("PUT", f"/user/ws/{resolved.id}")
        return resolved

    def list_datasources(self, *, workspace: int | str | Workspace | None = None) -> list[Datasource]:
        if workspace is not None:
            self.switch_workspace(workspace)
        payload = self._request("GET", "/datasource/list")
        return [Datasource.from_api(item) for item in payload]

    def resolve_datasource(
        self,
        datasource: int | str | Datasource,
        *,
        workspace: int | str | Workspace | None = None,
    ) -> Datasource:
        if isinstance(datasource, Datasource):
            return datasource
        datasources = self.list_datasources(workspace=workspace)
        text_ref = str(datasource).strip()
        if not text_ref:
            raise ConfigError("Datasource reference cannot be empty.")
        if text_ref.isdigit():
            datasource_id = int(text_ref)
            for item in datasources:
                if item.id == datasource_id:
                    return item
        lowered = text_ref.casefold()
        for item in datasources:
            if item.name == text_ref or item.name.casefold() == lowered:
                return item
        raise APIError(f"Datasource not found: {datasource}")

    def list_dashboards(
        self,
        *,
        workspace: int | str | Workspace | None = None,
        node_type: str | None = None,
    ) -> list[DashboardNode]:
        if workspace is not None:
            self.switch_workspace(workspace)
        payload: dict[str, Any] = {}
        if node_type:
            payload["node_type"] = node_type
        result = self._request("POST", "/dashboard/list_resource", payload=payload)
        return [DashboardNode.from_api(item) for item in result]

    def get_dashboard(
        self,
        dashboard_id: str,
        *,
        workspace: int | str | Workspace | None = None,
    ) -> dict[str, Any]:
        if workspace is not None:
            self.switch_workspace(workspace)
        if not dashboard_id:
            raise ConfigError("Dashboard ID is required.")
        result = self._request("POST", "/dashboard/load_resource", payload={"id": dashboard_id})
        if not isinstance(result, dict):
            raise APIError("Unexpected dashboard detail response.", payload=result)
        return result

    def start_chat(self, datasource: int | str | Datasource, *, question: str | None = None) -> dict[str, Any]:
        resolved = self.resolve_datasource(datasource)
        payload: dict[str, Any] = {"datasource": resolved.id}
        if question:
            payload["question"] = question
        result = self._request("POST", "/chat/start", payload=payload)
        if not isinstance(result, dict):
            raise APIError("Unexpected chat start response.", payload=result)
        return result

    def get_chat_record_data(self, record_id: int) -> Any:
        return self._request("GET", f"/chat/record/{record_id}/data")

    def ask_data(
        self,
        question: str,
        *,
        datasource: int | str | Datasource | None = None,
        chat_id: int | None = None,
    ) -> dict[str, Any]:
        if not question.strip():
            raise ConfigError("Question cannot be empty.")

        resolved_datasource: Datasource | None = None
        created_chat = False
        if datasource is not None:
            resolved_datasource = self.resolve_datasource(datasource)

        if chat_id is None:
            if resolved_datasource is None:
                raise ConfigError("Datasource is required when starting a new chat.")
            chat = self.start_chat(resolved_datasource)
            chat_id = int(chat["id"])
            created_chat = True

        payload: dict[str, Any] = {"chat_id": chat_id, "question": question}
        if resolved_datasource is not None:
            payload["datasource_id"] = resolved_datasource.id

        events = self._stream_request("POST", "/chat/question", payload=payload)
        result: dict[str, Any] = {
            "chat_id": chat_id,
            "created_chat": created_chat,
            "question": question,
            "datasource": resolved_datasource.to_dict() if resolved_datasource else None,
            "events": events,
        }

        record_id: int | None = None
        sql_answer_parts: list[str] = []
        chart_answer_parts: list[str] = []
        data_loaded = False

        for event in events:
            event_type = event.get("type")
            if event_type == "id":
                record_id = int(event["id"])
                result["record_id"] = record_id
            elif event_type == "brief":
                result["brief"] = event.get("brief")
            elif event_type == "question":
                result["question"] = event.get("question", question)
            elif event_type == "sql-result":
                sql_answer_parts.append(str(event.get("reasoning_content", "")))
            elif event_type == "sql":
                result["sql"] = event.get("content")
            elif event_type == "chart-result":
                chart_answer_parts.append(str(event.get("reasoning_content", "")))
            elif event_type == "chart":
                result["chart"] = event.get("content")
            elif event_type == "datasource" and result["datasource"] is None:
                result["datasource"] = {"id": event.get("id")}
            elif event_type == "error":
                result["error"] = event.get("content")
            elif event_type == "finish":
                result["finished"] = True
            elif event_type == "sql-data" and record_id is not None and not data_loaded:
                result["data"] = self.get_chat_record_data(record_id)
                data_loaded = True

        if sql_answer_parts:
            result["sql_answer"] = "".join(sql_answer_parts)
        if chart_answer_parts:
            result["chart_answer"] = "".join(chart_answer_parts)
        return result

    def _request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> Any:
        url = urljoin(f"{self.api_base_url}/", path.lstrip("/"))
        body: bytes | None = None
        headers = {"Accept": "application/json"}
        headers.update(self.build_auth_headers())
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url=url, data=body, method=method.upper(), headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            decoded = self._decode_body(body_text)
            detail = self._extract_error_message(decoded) or body_text or str(exc)
            raise APIError(detail, status_code=exc.code, payload=decoded) from exc
        except URLError as exc:
            raise APIError(f"Failed to reach SQLBot: {exc.reason}") from exc

        if not raw:
            return None

        decoded = self._decode_body(raw.decode("utf-8", errors="replace"))
        if isinstance(decoded, dict) and "code" in decoded:
            if decoded["code"] not in (0, 200):
                message = self._extract_error_message(decoded) or "SQLBot returned an error."
                raise APIError(message, payload=decoded)
            return decoded.get("data")
        return decoded

    def _stream_request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        url = urljoin(f"{self.api_base_url}/", path.lstrip("/"))
        body: bytes | None = None
        headers = {"Accept": "text/event-stream"}
        headers.update(self.build_auth_headers())
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url=url, data=body, method=method.upper(), headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            decoded = self._decode_body(body_text)
            detail = self._extract_error_message(decoded) or body_text or str(exc)
            raise APIError(detail, status_code=exc.code, payload=decoded) from exc
        except URLError as exc:
            raise APIError(f"Failed to reach SQLBot: {exc.reason}") from exc

        events: list[dict[str, Any]] = []
        body_text = raw.decode("utf-8", errors="replace")
        for chunk in body_text.split("\n\n"):
            stripped = chunk.strip()
            if not stripped:
                continue
            data_lines = [line[len("data:") :].lstrip() for line in stripped.splitlines() if line.startswith("data:")]
            if not data_lines:
                continue
            decoded = self._decode_body("\n".join(data_lines))
            if isinstance(decoded, dict) and "code" in decoded and decoded["code"] not in (0, 200):
                message = self._extract_error_message(decoded) or "SQLBot returned an error."
                raise APIError(message, payload=decoded)
            if isinstance(decoded, dict):
                events.append(decoded)
        return events

    @staticmethod
    def _decode_body(body: str) -> Any:
        if not body.strip():
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

    @staticmethod
    def _extract_error_message(payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key in ("detail", "message", "msg"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        if isinstance(payload, str) and payload:
            return payload
        return None


class DashboardExporter:
    _CACHE_MAX_EXPIRES_MS = 253402300799000

    def __init__(
        self,
        client: SQLBotClient,
        *,
        browser_path: str | None = None,
        ready_selector: str = "#sq-preview-content .canvas-container",
        wait_for_ms: int = 2000,
        timeout_ms: int = 45000,
        viewport_width: int = 1600,
        viewport_height: int = 900,
    ) -> None:
        self.client = client
        self.app_url = derive_app_url(client.api_base_url)
        self.browser_path = browser_path
        self.ready_selector = ready_selector
        self.wait_for_ms = wait_for_ms
        self.timeout_ms = timeout_ms
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height

    def build_preview_url(self, dashboard_id: str) -> str:
        return f"{self.app_url.rstrip('/')}/#/dashboard-preview?resourceId={quote(dashboard_id)}"

    def export_dashboard(
        self,
        dashboard_id: str,
        output_path: str | Path,
        *,
        export_format: str,
        workspace: int | str | Workspace | None = None,
    ) -> dict[str, Any]:
        resolved_workspace: Workspace | None = None
        if workspace is not None:
            resolved_workspace = self.client.switch_workspace(workspace)

        dashboard = self.client.get_dashboard(dashboard_id)
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        normalized_format = normalize_export_format(export_format)
        self._run_playwright_export(
            preview_url=self.build_preview_url(dashboard_id),
            output_path=destination,
            export_format=normalized_format,
            local_storage=self._build_local_storage(resolved_workspace),
        )
        return {
            "dashboard_id": dashboard_id,
            "dashboard_name": dashboard.get("name"),
            "workspace_id": resolved_workspace.id if resolved_workspace else dashboard.get("workspace_id"),
            "format": normalized_format,
            "output_path": str(destination),
        }

    def _build_local_storage(self, workspace: Workspace | None) -> dict[str, str]:
        storage = {"user.token": "api-key-auth", "user.language": "zh-CN"}
        if workspace is not None:
            storage["user.oid"] = str(workspace.id)
        return storage

    def _run_playwright_export(
        self,
        *,
        preview_url: str,
        output_path: Path,
        export_format: str,
        local_storage: dict[str, str],
    ) -> None:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise BrowserError(
                "Dashboard export requires Playwright. Install Chromium with `playwright install chromium`."
            ) from exc

        normalized_format = normalize_export_format(export_format)
        launch_options: dict[str, Any] = {"headless": True}
        if self.browser_path:
            launch_options["executable_path"] = self.browser_path

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**launch_options)
                context = browser.new_context(
                    viewport={"width": self.viewport_width, "height": self.viewport_height},
                    screen={"width": self.viewport_width, "height": self.viewport_height},
                )
                context.set_extra_http_headers(self.client.build_auth_headers())
                context.add_init_script(self._build_local_storage_init_script(local_storage))
                page = context.new_page()
                page.goto(preview_url, wait_until="domcontentloaded")
                page.wait_for_selector(self.ready_selector, state="visible", timeout=self.timeout_ms)
                page.wait_for_timeout(self.wait_for_ms)
                export_size = self._prepare_export_layout(page)
                if normalized_format in SCREENSHOT_EXPORT_FORMATS:
                    screenshot_options: dict[str, Any] = {"path": str(output_path), "full_page": True}
                    if normalized_format == "jpg":
                        screenshot_options["type"] = "jpeg"
                        screenshot_options["quality"] = 90
                    else:
                        screenshot_options["type"] = "png"
                    page.screenshot(**screenshot_options)
                else:
                    page.pdf(
                        path=str(output_path),
                        print_background=True,
                        prefer_css_page_size=True,
                        width=f"{export_size['width']}px",
                        height=f"{export_size['height']}px",
                        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                    )
                context.close()
                browser.close()
        except PlaywrightTimeoutError as exc:
            raise BrowserError(f"Timed out waiting for dashboard preview to render at {preview_url}.") from exc
        except PlaywrightError as exc:
            raise BrowserError(str(exc)) from exc

    def _build_local_storage_init_script(self, local_storage: dict[str, str]) -> str:
        return (
            "const storage = "
            + json.dumps(local_storage, ensure_ascii=False)
            + ";"
            + f"const maxExpires = {self._CACHE_MAX_EXPIRES_MS};"
            + "Object.entries(storage).forEach(([key, value]) => {"
            + "const cacheItem = { c: Date.now(), e: maxExpires, v: JSON.stringify(value) };"
            + "window.localStorage.setItem(key, JSON.stringify(cacheItem));"
            + "});"
        )

    def _prepare_export_layout(self, page: Any) -> dict[str, int]:
        export_size = page.evaluate(
            """(selector) => {
                const clamp = (value, fallback) => {
                  const number = Number(value)
                  if (!Number.isFinite(number) || number <= 0) return fallback
                  return Math.ceil(number)
                }
                const content = document.querySelector('#sq-preview-content')
                const canvas = document.querySelector(selector)
                const target = canvas || content || document.body
                const rect = target.getBoundingClientRect()
                const width = Math.max(
                  clamp(rect.width, 1),
                  clamp(target.scrollWidth, 1),
                  clamp(document.documentElement.scrollWidth, 1),
                  clamp(document.body.scrollWidth, 1)
                )
                const height = Math.max(
                  clamp(rect.height, 1),
                  clamp(target.scrollHeight, 1),
                  clamp(document.documentElement.scrollHeight, 1),
                  clamp(document.body.scrollHeight, 1)
                )

                ;[document.documentElement, document.body, content, canvas]
                  .filter(Boolean)
                  .forEach((element) => {
                    element.style.width = `${width}px`
                    element.style.minWidth = `${width}px`
                    element.style.maxWidth = `${width}px`
                    element.style.height = `${height}px`
                    element.style.minHeight = `${height}px`
                    element.style.maxHeight = `${height}px`
                    element.style.overflow = 'visible'
                  })

                const styleId = 'sqlbot-export-style'
                let styleTag = document.getElementById(styleId)
                if (!styleTag) {
                  styleTag = document.createElement('style')
                  styleTag.id = styleId
                  document.head.appendChild(styleTag)
                }
                styleTag.textContent = `
                  @page { size: ${width}px ${height}px; margin: 0; }
                  html, body { margin: 0; padding: 0; overflow: visible !important; }
                  #sq-preview-content, ${selector} {
                    overflow: visible !important;
                  }
                `

                return { width, height }
            }""",
            self.ready_selector,
        )
        page.set_viewport_size(export_size)
        page.wait_for_timeout(300)
        return {"width": int(export_size["width"]), "height": int(export_size["height"])}


class WorkspaceDashboardSkill:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        browser_path: str | None = None,
        state_file: str | None = None,
        timeout: float | None = None,
        api_key_ttl_seconds: int | None = None,
        env_file: str | None = None,
        openclaw_session_key: str | None = None,
        openclaw_session_id: str | None = None,
        openclaw_agent_id: str | None = None,
        allow_default_scope: bool = False,
    ) -> None:
        settings = SkillSettings.load(
            env_file=env_file,
            base_url=base_url,
            access_key=access_key,
            secret_key=secret_key,
            browser_path=browser_path,
            state_file=state_file,
            timeout=timeout,
            api_key_ttl_seconds=api_key_ttl_seconds,
        )
        self.settings = settings
        self.state_path = Path(settings.state_file)
        self.context = resolve_openclaw_context(
            session_key=openclaw_session_key,
            session_id=openclaw_session_id,
            agent_id=openclaw_agent_id,
        )
        self.state_store = SkillStateStore.load(self.state_path)
        self.client = SQLBotClient(
            base_url=settings.base_url,
            access_key=settings.access_key,
            secret_key=settings.secret_key,
            api_key_ttl_seconds=settings.api_key_ttl_seconds,
            timeout=settings.timeout,
        )
        self.exporter = DashboardExporter(self.client, browser_path=settings.browser_path)
        self.artifact_root = self.state_path.parent / "artifacts"
        self.allow_default_scope = allow_default_scope

    def list_workspaces(self) -> list[Workspace]:
        return self.client.list_workspaces()

    def switch_workspace(self, workspace: int | str | Workspace) -> Workspace:
        self._ensure_session_context(action="workspace switch")
        resolved = self.client.switch_workspace(workspace)
        session = self._session_state()
        workspace_changed = session.current_workspace is None or session.current_workspace.id != resolved.id
        session.current_workspace = resolved
        if workspace_changed:
            session.current_datasource = None
            session.clear_chat()
        self._save_session(session)
        return resolved

    def list_datasources(self, *, workspace: int | str | Workspace | None = None) -> list[Datasource]:
        self._ensure_session_context(action="datasource list")
        session = self._session_state()
        self._switch_workspace_if_requested(workspace, session=session)
        if workspace is None and session.current_workspace is not None:
            self.client.switch_workspace(session.current_workspace)
            self._save_session(session)
        return self.client.list_datasources()

    def switch_datasource(
        self,
        datasource: int | str | Datasource,
        *,
        workspace: int | str | Workspace | None = None,
    ) -> Datasource:
        self._ensure_session_context(action="datasource switch")
        session = self._session_state()
        resolved_workspace = self._switch_workspace_if_requested(workspace, session=session)
        resolved_datasource = self.client.resolve_datasource(datasource)
        session.current_workspace = resolved_workspace or session.current_workspace
        session.current_datasource = resolved_datasource
        session.clear_chat()
        self._save_session(session)
        return resolved_datasource

    def current_datasource(self) -> Datasource | None:
        self._ensure_session_context(action="datasource current")
        return self._session_state().current_datasource

    def list_dashboards(
        self,
        *,
        workspace: int | str | Workspace | None = None,
        node_type: str | None = None,
    ) -> list[DashboardNode]:
        self._ensure_session_context(action="dashboard list")
        session = self._session_state()
        self._switch_workspace_if_requested(workspace, session=session)
        if workspace is None and session.current_workspace is not None:
            self.client.switch_workspace(session.current_workspace)
        return self.client.list_dashboards(node_type=node_type)

    def view_dashboard(self, dashboard_id: str, *, workspace: int | str | Workspace | None = None) -> dict[str, Any]:
        self._ensure_session_context(action="dashboard show")
        session = self._session_state()
        self._switch_workspace_if_requested(workspace, session=session)
        if workspace is None and session.current_workspace is not None:
            self.client.switch_workspace(session.current_workspace)
        return self.client.get_dashboard(dashboard_id)

    def export_dashboard(
        self,
        dashboard_id: str,
        output_path: str | Path,
        *,
        export_format: str,
        workspace: int | str | Workspace | None = None,
    ) -> dict[str, Any]:
        self._ensure_session_context(action="dashboard export")
        session = self._session_state()
        self._switch_workspace_if_requested(workspace, session=session)
        if workspace is None and session.current_workspace is not None:
            self.client.switch_workspace(session.current_workspace)
        return self.exporter.export_dashboard(
            dashboard_id,
            output_path,
            export_format=export_format,
            workspace=None,
        )

    def ask_data(
        self,
        question: str,
        *,
        workspace: int | str | Workspace | None = None,
        datasource: int | str | Datasource | None = None,
        chat_id: int | None = None,
        include_events: bool = False,
        include_raw: bool = False,
        force_new_chat: bool = False,
        preview_rows: int = DEFAULT_PREVIEW_ROWS,
        top_n: int = DEFAULT_TOP_N,
    ) -> dict[str, Any]:
        self._ensure_session_context(action="ask")
        session = self._session_state()
        resolved_workspace = self._switch_workspace_if_requested(workspace, session=session)
        selected_datasource = datasource
        if selected_datasource is None and chat_id is None and not force_new_chat:
            state_workspace = session.current_workspace
            if session.current_datasource and (
                resolved_workspace is None
                or (state_workspace is not None and state_workspace.id == resolved_workspace.id)
            ):
                selected_datasource = session.current_datasource
        effective_chat_id = None if force_new_chat else (chat_id or session.sqlbot_chat_id)
        if effective_chat_id is None and selected_datasource is None:
            self._apply_default_binding(session)
            state_workspace = session.current_workspace
            if session.current_datasource and (
                resolved_workspace is None
                or (state_workspace is not None and resolved_workspace is not None and state_workspace.id == resolved_workspace.id)
                or resolved_workspace is None
            ):
                selected_datasource = session.current_datasource
        if effective_chat_id is None and selected_datasource is None:
            raise ConfigError(
                "No datasource is bound for this OpenClaw session. Run `datasource switch <id>` first or pass `--datasource`."
            )
        result = self.client.ask_data(question, datasource=selected_datasource, chat_id=effective_chat_id)
        next_datasource = None
        if selected_datasource is not None:
            next_datasource = self.client.resolve_datasource(selected_datasource)
        elif isinstance(result.get("datasource"), dict) and result["datasource"].get("id"):
            datasource_id = int(result["datasource"]["id"])
            datasource_list = self.client.list_datasources()
            next_datasource = next((item for item in datasource_list if item.id == datasource_id), None)
        fields, rows = _normalize_data_payload(result.get("data"))
        chart_payload = _parse_chart_payload(result.get("chart"))
        chart_plan = _choose_chart_plan(fields=fields, rows=rows, chart_payload=chart_payload, top_n=top_n)
        outcome = _build_ask_outcome(result=result, fields=fields, rows=rows, chart_plan=chart_plan)
        artifacts = self._build_ask_artifacts(
            question=question,
            fields=fields,
            rows=rows,
            result=result,
            chart_plan=chart_plan,
        )

        session.current_workspace = resolved_workspace or session.current_workspace
        session.current_datasource = next_datasource or session.current_datasource
        session.sqlbot_chat_id = _maybe_int(result.get("chat_id"))
        session.last_record_id = _maybe_int(result.get("record_id"))
        session.last_question = question
        session.last_sql = _coalesce(result.get("sql"))
        session.last_chart_kind = _coalesce(chart_plan.get("kind"))
        session.artifacts = {key: value for key, value in artifacts.items() if isinstance(value, str)}
        self._save_session(session)

        compact_payload = {
            "scope": self.context.to_dict(),
            "session": session.to_public_dict(),
            "summary": {
                "status": outcome["status"],
                "brief": result.get("brief"),
                "question": result.get("question", question),
                "row_count": len(rows),
                "fields": fields,
                "chart_kind": chart_plan.get("kind"),
                "summary_lines": outcome["summary_lines"],
                "error_reason": outcome["error_reason"],
                "user_hint": outcome["user_hint"],
                "rows_preview": rows[: max(1, preview_rows)],
                "sql_excerpt": (_coalesce(result.get("sql")) or "")[:1200],
            },
            "artifacts": artifacts,
            "source": {
                "record_id": result.get("record_id"),
                "chat_id": result.get("chat_id"),
                "datasource": result.get("datasource"),
            },
        }
        if include_raw:
            raw_payload = dict(result)
            if not include_events:
                raw_payload.pop("events", None)
            compact_payload["raw"] = raw_payload
        return compact_payload

    def session_status(self) -> dict[str, Any]:
        self._ensure_session_context(action="session show")
        session = self._session_state()
        return {
            "scope": self.context.to_dict(),
            "session": session.to_public_dict(),
        }

    def reset_session(self, *, full: bool = False) -> dict[str, Any]:
        self._ensure_session_context(action="session reset")
        session = self._session_state()
        session.clear_chat(full=full)
        self._save_session(session)
        return {
            "scope": self.context.to_dict(),
            "reset": True,
            "full": full,
            "session": session.to_public_dict(),
        }

    def _session_state(self) -> SessionState:
        return self.state_store.ensure_session(self.context)

    def _ensure_session_context(self, *, action: str) -> None:
        if self.allow_default_scope:
            return
        if self.context.session_key or self.context.session_id or self.context.scope_id != "default":
            return
        raise ConfigError(
            "Missing OpenClaw session context for "
            f"`{action}`. Call the OpenClaw `session_status` tool first, then rerun "
            "`sqlbot_skills.py` with `--openclaw-session-key <sessionKey> --openclaw-agent-id <agentId>`. "
            "Use `--allow-default-scope` only for standalone manual testing."
        )

    def _save_session(self, session: SessionState) -> None:
        session.bind_context(self.context)
        self.state_store.sessions[session.scope_id] = session
        self.state_store.save(self.state_path)

    def _apply_default_binding(self, session: SessionState) -> None:
        if session.current_workspace is not None and session.current_datasource is not None:
            return
        default_workspace = self.settings.default_workspace
        default_datasource = self.settings.default_datasource
        if not default_workspace or not default_datasource:
            return
        resolved_workspace = self.client.switch_workspace(default_workspace)
        resolved_datasource = self.client.resolve_datasource(default_datasource)
        session.current_workspace = resolved_workspace
        session.current_datasource = resolved_datasource
        self._save_session(session)

    def _switch_workspace_if_requested(
        self,
        workspace: int | str | Workspace | None,
        *,
        session: SessionState,
    ) -> Workspace | None:
        if workspace is None:
            return None
        resolved = self.client.switch_workspace(workspace)
        if session.current_workspace is None or session.current_workspace.id != resolved.id:
            session.current_workspace = resolved
            session.current_datasource = None
            session.clear_chat()
        else:
            session.current_workspace = resolved
        self._save_session(session)
        return resolved

    def _build_ask_artifacts(
        self,
        *,
        question: str,
        fields: list[str],
        rows: list[dict[str, Any]],
        result: dict[str, Any],
        chart_plan: dict[str, Any],
    ) -> dict[str, str | dict[str, Any] | None]:
        record_id = _maybe_int(result.get("record_id")) or 0
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        scope_dir = self.artifact_root / _slugify(self.context.scope_id, fallback="scope")
        artifact_dir = scope_dir / f"{timestamp}-record-{record_id or 'na'}"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        raw_path = artifact_dir / "raw-result.json"
        raw_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        normalized_path = artifact_dir / "normalized.json"
        normalized_payload = {
            "fields": fields,
            "row_count": len(rows),
            "rows": rows,
            "chart_plan": chart_plan,
            "question": question,
        }
        normalized_path.write_text(json.dumps(normalized_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        csv_path = None
        if fields and rows:
            csv_path = artifact_dir / "data.csv"
            _write_csv(csv_path, fields=fields, rows=rows)

        chart_path = None
        render_error = None
        if chart_plan.get("kind") not in {"empty", None}:
            chart_path = artifact_dir / "chart.png"
            try:
                _render_chart_artifact(chart_path, chart_plan=chart_plan)
            except Exception as exc:  # pragma: no cover - artifact failure should not break ask
                render_error = str(exc)
                chart_path = None

        artifacts: dict[str, str | dict[str, Any] | None] = {
            "directory": str(artifact_dir),
            "raw_json": str(raw_path),
            "normalized_json": str(normalized_path),
            "data_csv": str(csv_path) if csv_path else None,
            "chart_png": str(chart_path) if chart_path else None,
            "chart_plan": chart_plan,
        }
        if render_error:
            artifacts["render_error"] = render_error
        return artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sqlbot_skills.py",
        description="Workspace and dashboard skills for SQLBot.",
    )
    parser.add_argument("--env-file", default=None, help="Path to .env file.")
    parser.add_argument("--base-url", default=None, help="SQLBot base URL or API base URL.")
    parser.add_argument("--access-key", default=None, help="SQLBot API key access_key.")
    parser.add_argument("--secret-key", default=None, help="SQLBot API key secret_key.")
    parser.add_argument("--browser-path", default=None, help="Optional Chromium/Chrome executable path.")
    parser.add_argument("--state-file", default=None, help="Path to local datasource state file.")
    parser.add_argument("--timeout", type=float, default=None, help="HTTP timeout in seconds.")
    parser.add_argument("--openclaw-session-key", default=None, help="Optional explicit OpenClaw session key.")
    parser.add_argument("--openclaw-session-id", default=None, help="Optional explicit OpenClaw session id.")
    parser.add_argument("--openclaw-agent-id", default=None, help="Optional explicit OpenClaw agent id.")
    parser.add_argument(
        "--allow-default-scope",
        action="store_true",
        help="Allow standalone default-scope state when no OpenClaw session context is available.",
    )
    parser.add_argument(
        "--api-key-ttl-seconds",
        type=int,
        default=None,
        help="Signed API key token TTL in seconds.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    workspace_parser = subparsers.add_parser("workspace", help="Workspace operations.")
    workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_command", required=True)
    workspace_subparsers.add_parser("list", help="List accessible workspaces.")
    workspace_switch = workspace_subparsers.add_parser("switch", help="Switch current workspace.")
    workspace_switch.add_argument("workspace", help="Workspace id or exact name.")

    datasource_parser = subparsers.add_parser("datasource", help="Datasource operations.")
    datasource_subparsers = datasource_parser.add_subparsers(dest="datasource_command", required=True)
    datasource_list = datasource_subparsers.add_parser("list", help="List datasources.")
    datasource_list.add_argument("--workspace", help="Workspace id or exact name.")
    datasource_switch = datasource_subparsers.add_parser("switch", help="Switch current datasource locally.")
    datasource_switch.add_argument("datasource", help="Datasource id or exact name.")
    datasource_switch.add_argument("--workspace", help="Workspace id or exact name.")
    datasource_subparsers.add_parser("current", help="Show current datasource saved in the skill state.")

    session_parser = subparsers.add_parser("session", help="Session-scoped ask-data state.")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    session_subparsers.add_parser("show", help="Show current SQLBot binding for this OpenClaw session.")
    session_reset = session_subparsers.add_parser("reset", help="Reset the SQLBot chat binding for this OpenClaw session.")
    session_reset.add_argument("--full", action="store_true", help="Also clear the bound workspace and datasource.")

    dashboard_parser = subparsers.add_parser("dashboard", help="Dashboard operations.")
    dashboard_subparsers = dashboard_parser.add_subparsers(dest="dashboard_command", required=True)
    dashboard_list = dashboard_subparsers.add_parser("list", help="List dashboards.")
    dashboard_list.add_argument("--workspace", help="Workspace id or exact name.")
    dashboard_list.add_argument("--node-type", choices=["folder", "leaf"], help="Optional node type filter.")
    dashboard_list.add_argument("--flat", action="store_true", help="Flatten the dashboard tree before printing.")

    dashboard_show = dashboard_subparsers.add_parser("show", help="Show dashboard detail.")
    dashboard_show.add_argument("dashboard_id", help="Dashboard id.")
    dashboard_show.add_argument("--workspace", help="Workspace id or exact name.")

    dashboard_export = dashboard_subparsers.add_parser("export", help="Export dashboard as JPG/PNG screenshot or PDF.")
    dashboard_export.add_argument("dashboard_id", help="Dashboard id.")
    dashboard_export.add_argument("--workspace", help="Workspace id or exact name.")
    dashboard_export.add_argument(
        "--format",
        choices=["jpg", "jpeg", "png", "pdf"],
        default=DEFAULT_DASHBOARD_EXPORT_FORMAT,
        help=f"Export format. Defaults to {DEFAULT_DASHBOARD_EXPORT_FORMAT}.",
    )
    dashboard_export.add_argument("--output", help="Output file path. Defaults to ./<dashboard_id>.<format>.")

    ask_parser = subparsers.add_parser("ask", help="Ask a natural-language question against a datasource.")
    ask_parser.add_argument("question", nargs="+", help="Natural-language question.")
    ask_parser.add_argument("--workspace", help="Workspace id or exact name.")
    ask_parser.add_argument("--datasource", help="Datasource id or exact name.")
    ask_parser.add_argument("--chat-id", type=int, help="Existing chat id to continue.")
    ask_parser.add_argument("--include-events", action="store_true", help="Include raw SSE events in the output payload.")
    ask_parser.add_argument("--include-raw", action="store_true", help="Include the raw SQLBot response in the output payload.")
    ask_parser.add_argument("--new-chat", action="store_true", help="Force a new SQLBot chat inside the current OpenClaw session.")
    ask_parser.add_argument("--preview-rows", type=int, default=DEFAULT_PREVIEW_ROWS, help="Number of preview rows to print in compact output.")
    ask_parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Top N rows to use for generated charts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        skill = WorkspaceDashboardSkill(
            env_file=args.env_file,
            base_url=args.base_url,
            access_key=args.access_key,
            secret_key=args.secret_key,
            browser_path=args.browser_path,
            state_file=args.state_file,
            timeout=args.timeout,
            api_key_ttl_seconds=args.api_key_ttl_seconds,
            openclaw_session_key=args.openclaw_session_key,
            openclaw_session_id=args.openclaw_session_id,
            openclaw_agent_id=args.openclaw_agent_id,
            allow_default_scope=args.allow_default_scope,
        )
        if args.command == "workspace":
            return _run_workspace(skill, args)
        if args.command == "datasource":
            return _run_datasource(skill, args)
        if args.command == "session":
            return _run_session(skill, args)
        if args.command == "dashboard":
            return _run_dashboard(skill, args)
        if args.command == "ask":
            return _run_ask(skill, args)
        parser.error("Unknown command.")
        return 2
    except APIError as exc:
        print(f"SQLBot 执行失败：{_summarize_execution_error(str(exc))}", file=sys.stderr)
        return 1
    except SQLBotSkillError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _run_workspace(skill: WorkspaceDashboardSkill, args: argparse.Namespace) -> int:
    if args.workspace_command == "list":
        _print_json([item.to_dict() for item in skill.list_workspaces()])
        return 0
    if args.workspace_command == "switch":
        workspace = skill.switch_workspace(args.workspace)
        _print_json({"switched": True, "workspace": workspace.to_dict()})
        return 0
    raise AssertionError("Unhandled workspace command.")


def _run_datasource(skill: WorkspaceDashboardSkill, args: argparse.Namespace) -> int:
    if args.datasource_command == "list":
        _print_json([item.to_dict() for item in skill.list_datasources(workspace=args.workspace)])
        return 0
    if args.datasource_command == "switch":
        datasource = skill.switch_datasource(args.datasource, workspace=args.workspace)
        _print_json({"switched": True, "datasource": datasource.to_dict()})
        return 0
    if args.datasource_command == "current":
        datasource = skill.current_datasource()
        _print_json(datasource.to_dict() if datasource else None)
        return 0
    raise AssertionError("Unhandled datasource command.")


def _run_dashboard(skill: WorkspaceDashboardSkill, args: argparse.Namespace) -> int:
    if args.dashboard_command == "list":
        dashboards = skill.list_dashboards(workspace=args.workspace, node_type=args.node_type)
        payload = [item.to_dict() for item in (_flatten_nodes(dashboards) if args.flat else dashboards)]
        _print_json(payload)
        return 0
    if args.dashboard_command == "show":
        _print_json(skill.view_dashboard(args.dashboard_id, workspace=args.workspace))
        return 0
    if args.dashboard_command == "export":
        export_format = normalize_export_format(args.format)
        output = args.output or f"./{args.dashboard_id}.{export_format}"
        payload = skill.export_dashboard(
            args.dashboard_id,
            output,
            export_format=export_format,
            workspace=args.workspace,
        )
        _print_json(payload)
        return 0
    raise AssertionError("Unhandled dashboard command.")


def _run_session(skill: WorkspaceDashboardSkill, args: argparse.Namespace) -> int:
    if args.session_command == "show":
        _print_json(skill.session_status())
        return 0
    if args.session_command == "reset":
        _print_json(skill.reset_session(full=bool(args.full)))
        return 0
    raise AssertionError("Unhandled session command.")


def _run_ask(skill: WorkspaceDashboardSkill, args: argparse.Namespace) -> int:
    payload = skill.ask_data(
        " ".join(args.question),
        workspace=args.workspace,
        datasource=args.datasource,
        chat_id=args.chat_id,
        include_events=args.include_events,
        include_raw=args.include_raw,
        force_new_chat=bool(args.new_chat),
        preview_rows=max(1, int(args.preview_rows)),
        top_n=max(1, int(args.top_n)),
    )
    _print_json(payload)
    return 0


def _flatten_nodes(nodes: Iterable[DashboardNode]) -> list[DashboardNode]:
    flattened: list[DashboardNode] = []
    for node in nodes:
        flattened.extend(node.walk())
    return flattened


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
