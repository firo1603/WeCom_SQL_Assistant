"""
Scan artifacts/ directory — read-only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def _read_manifest(record_dir: Path) -> dict:
    manifest_path = record_dir / "manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _read_sql_from_raw(record_dir: Path) -> Optional[str]:
    """Fallback: read sql field from raw-result.json if not in manifest."""
    raw_path = record_dir / "raw-result.json"
    if not raw_path.exists():
        return None
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        return raw.get("sql") or None
    except Exception:
        return None


def get_artifact_summary(artifacts_dir: str, trace_id: Optional[str] = None) -> list[dict]:
    base = Path(artifacts_dir)
    if not base.exists():
        return []

    results = []
    for scope_dir in sorted(base.iterdir()):
        if not scope_dir.is_dir():
            continue
        for record_dir in sorted(scope_dir.iterdir(), reverse=True):
            if not record_dir.is_dir():
                continue

            manifest = _read_manifest(record_dir)

            if trace_id and manifest.get("trace_id") != trace_id:
                continue

            results.append({
                "scope": scope_dir.name,
                "record_dir": record_dir.name,
                "trace_id": manifest.get("trace_id"),
                "question": manifest.get("question"),
                "status": manifest.get("status"),
                "row_count": manifest.get("row_count"),
                "chart_kind": manifest.get("chart_kind"),
                "files": {
                    "raw_result": (record_dir / "raw-result.json").exists(),
                    "normalized": (record_dir / "normalized.json").exists(),
                    "csv": (record_dir / "data.csv").exists(),
                    "chart": (record_dir / "chart.png").exists(),
                    "manifest": (record_dir / "manifest.json").exists(),
                },
                "manifest": manifest,
            })

    return results


def get_conversations(
    artifacts_dir: str,
    aliases: Optional[dict] = None,
    filters: Optional[dict] = None,
) -> list[dict]:
    """
    Return a flat list of conversations (one per artifact record), ordered by time desc.
    Falls back to normalized.json for question, and raw-result.json for SQL.

    aliases: {user_short: display_name} — server-side from aliases.json
    filters: {q, user, status, date, datasource} for server-side filtering
    """
    base = Path(artifacts_dir)
    if not base.exists():
        return []

    if aliases is None:
        aliases = {}
    if filters is None:
        filters = {}

    f_q = (filters.get("q") or "").strip().lower()
    f_user = (filters.get("user") or "").strip().lower()
    f_status = (filters.get("status") or "").strip().lower()
    f_date = (filters.get("date") or "").strip()          # YYYY-MM-DD prefix match
    f_ds = (filters.get("datasource") or "").strip().lower()

    records = []
    for scope_dir in base.iterdir():
        if not scope_dir.is_dir():
            continue
        for record_dir in scope_dir.iterdir():
            if not record_dir.is_dir():
                continue

            manifest = _read_manifest(record_dir)

            # Fallback: read question from normalized.json
            question = manifest.get("question") or ""
            if not question:
                norm_path = record_dir / "normalized.json"
                if norm_path.exists():
                    try:
                        norm = json.loads(norm_path.read_text(encoding="utf-8"))
                        question = norm.get("question") or ""
                    except Exception:
                        pass

            # Infer created_at from directory name if manifest doesn't have it
            # dir name format: YYYYMMDD-HHMMSS-record-{id}
            created_at = manifest.get("created_at", "")
            if not created_at:
                name = record_dir.name
                try:
                    # e.g. "20260423-104500-record-12"
                    date_part = name[:8]   # 20260423
                    time_part = name[9:15] # 104500
                    if date_part.isdigit() and time_part.isdigit():
                        created_at = (
                            f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
                            f"T{time_part[:2]}:{time_part[2:4]}:{time_part[4:6]}"
                        )
                except Exception:
                    created_at = ""

            # Skip records with absolutely no useful data
            if not question and not created_at and not manifest:
                continue

            sql = manifest.get("sql") or _read_sql_from_raw(record_dir)
            session_key = manifest.get("session_key") or ""
            datasource = manifest.get("datasource") or ""
            status = manifest.get("status") or ""

            # Extract short name from session_key
            # e.g. "agent:corp-assistant:wecom:direct:lingxiaodong" → "lingxiaodong"
            if "direct:" in session_key:
                user_short = session_key.split("direct:")[-1].strip()
            elif ":" in session_key:
                user_short = session_key.rsplit(":", 1)[-1].strip()
            else:
                user_short = session_key

            user_display = aliases.get(user_short) or user_short

            # ── server-side filtering ───────────────────────────────────────
            if f_status and status.lower() != f_status:
                continue
            if f_date and not created_at.startswith(f_date):
                continue
            if f_ds and f_ds not in datasource.lower():
                continue
            if f_user:
                if (
                    f_user not in user_short.lower()
                    and f_user not in user_display.lower()
                    and f_user not in session_key.lower()
                ):
                    continue
            if f_q and f_q not in question.lower():
                continue

            records.append({
                "created_at": created_at,
                "session_key": session_key,
                "user_short": user_short,
                "user_display": user_display,
                "workspace": manifest.get("workspace", ""),
                "datasource": datasource,
                "question": question,
                "sql": sql,
                "summary_lines": manifest.get("summary_lines"),
                "status": status,
                "error_kind": manifest.get("error_kind"),
                "error_reason": manifest.get("error_reason"),
                "row_count": manifest.get("row_count"),
                "chart_kind": manifest.get("chart_kind"),
                "trace_id": manifest.get("trace_id"),
            })

    records.sort(key=lambda x: x["created_at"], reverse=True)
    return records
