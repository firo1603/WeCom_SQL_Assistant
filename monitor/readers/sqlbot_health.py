"""
SQLBot health probe — parse SQLBOT_BASE_URL from skill .env, then HTTP GET.
Read-only; no writes to any production file.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import httpx


def _parse_env(env_path: str) -> dict:
    path = Path(env_path)
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            val = m.group(2).strip().strip('"').strip("'")
            env[m.group(1)] = val
    return env


async def check_sqlbot_health(skill_env: str, timeout: int = 5) -> dict:
    env = _parse_env(skill_env)
    base_url = env.get("SQLBOT_BASE_URL", "").rstrip("/")

    if not base_url:
        return {
            "reachable": False,
            "base_url": None,
            "status_code": None,
            "error": ".env missing SQLBOT_BASE_URL",
        }

    # Probe the datasource list endpoint (unauthenticated ping)
    probe_url = f"{base_url}/api/v1/datasource/list"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(probe_url)
        return {
            "reachable": resp.status_code < 500,
            "base_url": base_url,
            "status_code": resp.status_code,
            "error": None,
        }
    except httpx.ConnectError as e:
        return {
            "reachable": False,
            "base_url": base_url,
            "status_code": None,
            "error": f"连接失败: {e}",
        }
    except Exception as e:
        return {
            "reachable": False,
            "base_url": base_url,
            "status_code": None,
            "error": str(e),
        }
