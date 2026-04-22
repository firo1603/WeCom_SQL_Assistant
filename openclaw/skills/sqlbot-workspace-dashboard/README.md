# sqlbot-workspace-dashboard

This local skill install mirrors the upstream SQLBot skill interface and command layout for OpenClaw workspace use.

Source repository:

`https://github.com/dataease/SQLBot-skills`

## Files

- `SKILL.md`: Agent Skill entrypoint
- `sqlbot_skills.py`: CLI and API client
- `.env.example`: SQLBot connection template
- `reference.md`: short usage notes

## Quick start

1. Copy `.env.example` to `.env`
2. Fill in `SQLBOT_BASE_URL`, `SQLBOT_API_KEY_ACCESS_KEY`, and `SQLBOT_API_KEY_SECRET_KEY`
3. Run a smoke test:

```bash
python3 sqlbot_skills.py workspace list
```

## Export support

Image/PDF export uses Playwright. If Chromium is missing in your environment, install it with:

```bash
playwright install chromium
```

## Artifacts

Each `ask` call writes the following files into the `artifacts/` directory alongside the skill:

```text
artifacts/
  <scope_id>/
    <YYYYMMDD-HHMMSS>-record-<id>/
      raw-result.json      # raw SQLBot API response
      normalized.json      # normalized fields, rows, chart plan
      data.csv             # tabular data (if rows returned)
      chart.png            # rendered chart (if chart plan available)
      manifest.json        # trace linkage, session info, artifact index
```

## Structured tracing

The skill supports structured execution tracing via optional CLI flags:

```bash
# Emit trace events to monitoring/sqlbot-events.jsonl (relative to skill dir)
python3 sqlbot_skills.py --emit-trace ask "<question>"

# Use a custom trace file
python3 sqlbot_skills.py --trace-file /path/to/events.jsonl ask "<question>"

# Use a custom trace ID
python3 sqlbot_skills.py --trace-id "my-trace-001" --emit-trace ask "<question>"
```

Trace events are written as JSONL, one event per line, one line per execution stage.

The `ask` result also includes a top-level `telemetry` field with:
- `trace_id`
- `started_at` / `finished_at`
- `duration_ms`
- `stage_durations_ms` (per-stage breakdown)

## Error classification

`ask` results include `summary.error_kind` with a stable machine-readable value:

| `error_kind` | Meaning |
|---|---|
| `auth_error` | API key invalid or unauthorized |
| `config_error` | Workspace or datasource not found |
| `network_error` | Cannot reach SQLBot service |
| `sql_execution_error` | SQL generation or execution failure |
| `timeout` | Request timed out |
| `sqlbot_api_error` | Other SQLBot API error |
| `empty_result` | Query succeeded but returned no rows |
| `null` | Query succeeded with data |
