"""
Read sqlbot-events.jsonl trace file.
All operations are read-only.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional


# ─── file helpers ────────────────────────────────────────────────────────────

def _tail_lines(filepath: str, n: int) -> list[str]:
    path = Path(filepath)
    if not path.exists():
        return []
    size = path.stat().st_size
    if size == 0:
        return []
    block = 65536
    lines: list[bytes] = []
    remainder = b""
    pos = size
    with path.open("rb") as f:
        while pos > 0 and len(lines) < n + 1:
            read_size = min(block, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size) + remainder
            parts = chunk.split(b"\n")
            remainder = parts[0]
            lines = parts[1:] + lines
    if remainder:
        lines = [remainder] + lines
    return [
        ln.decode("utf-8", errors="replace")
        for ln in lines[-n:]
        if ln.strip()
    ]


def load_events(trace_file: str, tail_lines: int = 5000) -> list[dict]:
    lines = _tail_lines(trace_file, tail_lines)
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# ─── overview ────────────────────────────────────────────────────────────────

def get_overview(events: list[dict]) -> dict:
    today = date.today().isoformat()
    today_events = [e for e in events if e.get("ts", "").startswith(today)]

    # Count by ask.finish events (one per request)
    finish_events = [e for e in today_events if e.get("stage") == "ask.finish"]

    total = len(finish_events)
    ok = sum(1 for e in finish_events if e.get("status") == "ok")
    empty = sum(1 for e in finish_events if e.get("error_kind") == "empty_result")
    errors = sum(1 for e in finish_events if e.get("status") == "error")

    durations = [e["duration_ms"] for e in finish_events if "duration_ms" in e]
    avg_ms = int(sum(durations) / len(durations)) if durations else 0
    p95_ms = int(sorted(durations)[int(len(durations) * 0.95)]) if durations else 0

    # Distinct active sessions today
    sessions = {e["session_key"] for e in today_events if e.get("session_key")}

    # Recent errors (most recent first)
    error_events = sorted(
        [e for e in finish_events if e.get("status") == "error"],
        key=lambda x: x.get("ts", ""),
        reverse=True,
    )[:5]

    return {
        "total": total,
        "ok": ok,
        "empty": empty,
        "errors": errors,
        "success_rate": round(ok / total * 100, 1) if total else 0,
        "avg_ms": avg_ms,
        "p95_ms": p95_ms,
        "active_sessions": len(sessions),
        "recent_errors": error_events,
    }


# ─── trace list ──────────────────────────────────────────────────────────────

def get_trace_list(events: list[dict], filters: Optional[dict] = None) -> list[dict]:
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        tid = e.get("trace_id")
        if tid:
            by_trace[tid].append(e)

    traces = []
    for trace_id, evs in by_trace.items():
        finish = next(
            (e for e in evs if e.get("stage") == "ask.finish"),
            max(evs, key=lambda x: x.get("ts", ""), default=None),
        )
        if not finish:
            continue

        row = {
            "trace_id": trace_id,
            "ts": finish.get("ts", ""),
            "session_key": finish.get("session_key", ""),
            "workspace": finish.get("workspace", ""),
            "datasource": finish.get("datasource", ""),
            "chat_id": finish.get("chat_id"),
            "record_id": finish.get("record_id"),
            "status": finish.get("status", ""),
            "error_kind": finish.get("error_kind"),
            "duration_ms": finish.get("duration_ms"),
        }

        if filters:
            if filters.get("status") and row["status"] != filters["status"]:
                continue
            if filters.get("session") and filters["session"] not in (row["session_key"] or ""):
                continue
            if filters.get("date") and not row["ts"].startswith(filters["date"]):
                continue

        traces.append(row)

    traces.sort(key=lambda x: x["ts"], reverse=True)
    return traces


# ─── trace detail ────────────────────────────────────────────────────────────

def get_trace_detail(events: list[dict], trace_id: str) -> Optional[dict]:
    evs = sorted(
        [e for e in events if e.get("trace_id") == trace_id],
        key=lambda x: x.get("ts", ""),
    )
    if not evs:
        return None

    finish = next((e for e in evs if e.get("stage") == "ask.finish"), evs[-1])

    # Build waterfall: compute relative ms from first event
    first_ts_str = evs[0].get("ts", "")
    try:
        first_ts = datetime.fromisoformat(first_ts_str.replace("Z", "+00:00"))
    except Exception:
        first_ts = None

    stages = []
    for e in evs:
        duration_ms = e.get("duration_ms", 0) or 0
        end_rel = None
        start_rel = None
        if first_ts and e.get("ts"):
            try:
                evt_ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
                end_rel = int((evt_ts - first_ts).total_seconds() * 1000)
                start_rel = max(0, end_rel - duration_ms)
            except Exception:
                pass
        stages.append({
            "stage": e.get("stage"),
            "ts": e.get("ts"),
            "status": e.get("status"),
            "duration_ms": duration_ms,
            "error_kind": e.get("error_kind"),
            "error_message": e.get("error_message"),
            "start_rel_ms": start_rel,
            "end_rel_ms": end_rel,
        })

    return {
        "trace_id": trace_id,
        "ts": finish.get("ts", ""),
        "session_key": finish.get("session_key", ""),
        "workspace": finish.get("workspace", ""),
        "datasource": finish.get("datasource", ""),
        "chat_id": finish.get("chat_id"),
        "record_id": finish.get("record_id"),
        "status": finish.get("status", ""),
        "error_kind": finish.get("error_kind"),
        "error_message": finish.get("error_message"),
        "duration_ms": finish.get("duration_ms"),
        "stages": stages,
    }
