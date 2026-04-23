"""
Read .sqlbot-skill-state.json — read-only.
"""
from __future__ import annotations

import json
from pathlib import Path


def _load(state_file: str) -> dict:
    path = Path(state_file)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_sessions(state_file: str) -> list[dict]:
    state = _load(state_file)
    sessions = []

    for scope_key, scope_data in state.items():
        if not isinstance(scope_data, dict):
            continue

        workspace = scope_data.get("workspace") or scope_data.get("current_workspace")
        datasource = scope_data.get("datasource") or scope_data.get("current_datasource")
        chat_id = scope_data.get("chat_id") or scope_data.get("current_chat_id")
        record_id = scope_data.get("record_id") or scope_data.get("last_record_id")
        last_question = scope_data.get("last_question")
        updated_at = scope_data.get("updated_at")

        anomalies: list[str] = []
        if not datasource:
            anomalies.append("datasource_missing")
        if "default" in scope_key.lower():
            anomalies.append("default_scope")
        if not chat_id:
            anomalies.append("no_chat_id")

        sessions.append({
            "scope_key": scope_key,
            "workspace": workspace,
            "datasource": datasource,
            "chat_id": chat_id,
            "record_id": record_id,
            "last_question": last_question,
            "updated_at": updated_at,
            "anomalies": anomalies,
        })

    sessions.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return sessions
