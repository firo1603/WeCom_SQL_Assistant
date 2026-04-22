---
name: sqlbot-workspace-dashboard
description: Use this skill for SQLBot ask-data workflows in OpenClaw. In production `corp-assistant`, this is the default route for natural-language business/data questions and follow-up analysis. It binds one SQLBot chat to one OpenClaw session, with workspace or datasource selection, compact answers, generated chart artifacts, and dashboard utilities.
argument-hint: "[session show|session reset [--full]|list-workspaces|switch-workspace <workspace>|list-datasources [--workspace <workspace>]|switch-datasource <datasource> [--workspace <workspace>]|ask <question>|ask --new-chat <question>|list-dashboards [--workspace <workspace>]|show-dashboard <dashboard-id> [--workspace <workspace>]|export-dashboard <dashboard-id> [--format jpg|png|pdf] [--workspace <workspace>] [--output <path>]]"
allowed-tools: Bash(python3 *), Read, Glob, Grep
---

# SQLBot Session Workflow Skill

Use this skill when the user wants to ask natural-language data questions against SQLBot, continue a follow-up question in the same chat, switch the SQLBot datasource bound to the current OpenClaw session, or export a dashboard.

This skill wraps `${CLAUDE_SKILL_DIR}/sqlbot_skills.py`.

## Before running

1. Check whether `${CLAUDE_SKILL_DIR}/.env` exists.
2. If it does not exist, tell the user to copy `.env.example` to `.env` and fill:
   - `SQLBOT_BASE_URL`
   - `SQLBOT_API_KEY_ACCESS_KEY`
   - `SQLBOT_API_KEY_SECRET_KEY`
3. For dashboard export and ask-result chart rendering, no extra browser dependency is required now.

## Default workflow

- Treat `ask` as the main path.
- For production `corp-assistant`, use this skill by default for natural-language business/data questions and analytical follow-ups.
- No `查询` prefix is required.
- If the current message is only a greeting, a capability question, a help request, or a clearly non-data request, do not invoke this skill.
- One OpenClaw session maps to one SQLBot ask-data chat.
- For production `corp-assistant`, always call the OpenClaw `session_status` tool first and read `details.sessionKey`.
- For production `corp-assistant`, every session-scoped `sqlbot_skills.py` command must include:
  - `--openclaw-session-key "<sessionKey>"`
  - `--openclaw-agent-id "corp-assistant"`
- Never use implicit `default` scope for production user traffic.
- The first ask in a session must know the datasource:
  - either the user already switched datasource in this session
  - or you pass `--datasource`
- In this production workspace, if no session datasource is bound yet, the skill may auto-bind the configured default workspace/datasource from `.env`.
- Follow-up asks in the same OpenClaw session should usually call plain `ask "<question>"` and let the skill reuse the bound SQLBot `chat_id`.
- If the user changes workspace or datasource, the skill resets the SQLBot chat binding automatically.
- If the user says to start over, use `session reset`. Use `session reset --full` only when you should also clear workspace and datasource selection.

## Preferred commands

- First read the current OpenClaw session key from the `session_status` tool, then reuse it in every shell command below.

- Show current SQLBot binding for this OpenClaw session:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  session show
```

- Reset the SQLBot chat binding for this OpenClaw session:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  session reset
```

- Fully clear workspace and datasource too:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  session reset --full
```

- List workspaces:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" workspace list
```

- Bind workspace for the current OpenClaw session:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  workspace switch "<workspace>"
```

- List datasources:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  datasource list --workspace "<workspace>"
```

- Bind datasource for the current OpenClaw session:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  datasource switch "<datasource>" --workspace "<workspace>"
```

- First ask with explicit datasource:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  ask "<question>" --datasource "<datasource>" --workspace "<workspace>"
```

- Follow-up ask in the same OpenClaw session:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  ask "<question>"
```

- Force a brand-new SQLBot chat while staying in the same OpenClaw session:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  ask --new-chat "<question>"
```

- List dashboards:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  dashboard list --workspace "<workspace>"
```

- Show dashboard detail:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  dashboard show "<dashboard-id>" --workspace "<workspace>"
```

- Export dashboard:

```bash
python3 "${CLAUDE_SKILL_DIR}/sqlbot_skills.py" \
  --openclaw-session-key "<sessionKey>" \
  --openclaw-agent-id "corp-assistant" \
  dashboard export "<dashboard-id>" --workspace "<workspace>" --output "./dashboard.jpg"
```

## What `ask` returns

`ask` now returns compact JSON by default:

- `scope`: OpenClaw session binding used by the skill
- `session`: current bound workspace, datasource, SQLBot `chat_id`, last record id
- `summary`: compact answer metadata, preview rows, SQL excerpt, chart kind
  - `summary.status`: `ok` / `empty` / `error`
  - `summary.error_kind`: machine-readable error class (`auth_error`, `config_error`, `network_error`, `sql_execution_error`, `timeout`, `sqlbot_api_error`, `empty_result`, or `null` for ok)
  - `summary.error_reason`: human-readable error summary
  - `summary.user_hint`: suggested next action for the user
- `artifacts`:
  - `chart_png`
  - `data_csv`
  - `raw_json`
  - `normalized_json`
  - `manifest_json`: trace linkage, session info, artifact file index
- `source`: SQLBot record id, datasource, chat id
- `telemetry`: trace ID, started/finished timestamps, total duration, per-stage duration breakdown

Do not dump the raw JSON back to the user unless they ask for it. Read the compact fields and summarize the result.
Do not expose `telemetry` or `artifacts` paths to end users in normal operation.

## Execution rules

- Prefer exact workspace names or numeric IDs.
- Prefer exact datasource names or numeric IDs.
- Use `session show` when you are not sure what is already bound.
- In production `corp-assistant`, infer SQLBot intent from the user's likely business/data request and session context; do not require an explicit trigger prefix.
- Skip this skill for pure greeting/help/capability turns and for clearly out-of-scope non-data requests.
- If `sqlbot_skills.py` reports missing OpenClaw session context, call `session_status` and retry with explicit `--openclaw-session-key` and `--openclaw-agent-id`.
- For production requests, prefer `ask "<question>"` even when the question is phrased in broad business language rather than explicit metric language.
- Prefer plain `ask "<question>"` for follow-ups in the same chat.
- Use `--new-chat` or `session reset` when the user wants a fresh analysis thread.
- `workspace switch` and `datasource switch` are session-scoped and invalidate the old SQLBot chat binding.
- Only use dashboard commands when the user explicitly wants a dashboard, screenshot, or PDF export.
- If a SQLBot command exits non-zero or returns a generic error, describe it as a SQLBot execution failure.
- Do not rewrite a generic error into a business conclusion such as “the datasource has no such data” unless the payload explicitly proves that.
- After `ask`, read `summary.status` first:
  - `ok`: summarize the result normally
  - `empty`: tell the user the query executed successfully but no matching data was returned
  - `error`: return the brief reason from `summary.error_reason` and the usage hint from `summary.user_hint`. Use `summary.error_kind` for programmatic routing — do not guess error type from human-readable text.
- Never describe `summary.status = error` as "没有查询到数据".
- For manual shell runs outside OpenClaw, you may pass:
  - `--openclaw-session-key`
  - `--openclaw-session-id`
  - `--openclaw-agent-id`
  - `--allow-default-scope`
  These are mainly for testing and explicit session routing.
- To enable structured trace emission (writes JSONL to `monitoring/sqlbot-events.jsonl` in the skill directory):
  - `--emit-trace`: enable tracing with auto-generated trace ID
  - `--trace-id <id>`: override trace ID
  - `--trace-file <path>`: write trace events to a custom file path
  These flags are optional and intended for observability. Do not require them for normal production use.

## Additional resources

- Detailed runtime notes and command reference: [reference.md](reference.md)
- Upstream repository overview: [README.md](README.md)
