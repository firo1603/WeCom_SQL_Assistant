---
name: sqlbot-workspace-dashboard
description: Use this skill for SQLBot ask-data workflows in OpenClaw agents. It binds one SQLBot chat to one OpenClaw session, with workspace or datasource selection, compact answers, generated chart artifacts, and dashboard utilities.
argument-hint: "[session show|session reset [--full]|list-workspaces|switch-workspace <workspace>|list-datasources [--workspace <workspace>]|switch-datasource <datasource> [--workspace <workspace>]|ask <question>|ask --new-chat <question>|list-dashboards [--workspace <workspace>]|show-dashboard <dashboard-id> [--workspace <workspace>]|export-dashboard <dashboard-id> [--format jpg|png|pdf] [--workspace <workspace>] [--output <path>]]"
allowed-tools: Bash(python3 *), Read, Glob, Grep
---

# SQLBot Session Workflow Skill

Use this skill when a SQLBot-enabled OpenClaw agent receives any concrete business/data question or request, continues a follow-up question in the same chat, switches the SQLBot datasource bound to the current OpenClaw session, or exports a dashboard.

In agents configured to route business/data traffic through SQLBot, this skill is the default execution path. Pure greetings, capability introductions, and help/scope explanations may be answered directly, but every other concrete user request should be routed to SQLBot first. Do not maintain a whitelist of topics; current working examples such as freight forwarding document volume, inspection rate, commodity tax-rate information, agency agreements, and single-ticket lookup are non-exhaustive.

This skill wraps `sqlbot_skills.py` in this installed skill directory.

## OpenClaw exec preflight requirements

Use this command template, resolving placeholders before execution. OpenClaw exec accepts this skill only when the final command is invoked as a direct interpreter command:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" ask "<question>"
```

- Before invoking the script, resolve:
  - `<skillDir>`: absolute path of this installed skill directory, the directory containing this `SKILL.md`
  - `<scriptPath>`: `<skillDir>/sqlbot_skills.py`
  - `<sessionKey>`: `session_status.details.sessionKey`
  - `<agentId>`: `session_status.details.agentId` when available; otherwise parse it from `details.sessionKey` when it has the form `agent:<agentId>:...`
- The final executed shell command must contain the literal absolute `<scriptPath>` and literal session values.
- Do not execute commands that still contain `<scriptPath>`, `<skillDir>`, or other placeholders.
- The command must be one direct `python3 <absolute .py path> ...` command.
- Do not use `cd`, `&&`, `;`, pipes, shell wrappers, shell variables, command substitution, heredocs, `python -c`, `python -m`, relative script paths, or multi-command retries.
- Do not drop or rewrite the session key. Use `details.sessionKey` exactly, including the `agent:` prefix.
- If exec reports `complex interpreter invocation detected`, retry once with the direct absolute-path form above.

## Before running

1. Check whether `.env` exists next to this `SKILL.md` in the installed skill directory.
2. If it does not exist, tell the user to copy `.env.example` to `.env` and fill:
   - `SQLBOT_BASE_URL`
   - `SQLBOT_API_KEY_ACCESS_KEY`
   - `SQLBOT_API_KEY_SECRET_KEY`
3. For dashboard export and ask-result chart rendering, no extra browser dependency is required now.

## Default workflow

- Treat `ask` as the main path.
- For SQLBot-enabled production agents, use this skill by default for every concrete non-greeting request and analytical follow-up.
- Do not screen requests by topic before invoking SQLBot. If the user asks a concrete question, call `ask` first and let SQLBot determine whether the datasource can answer.
- No `查询` prefix is required.
- If the current message is only a greeting, a capability question, a help request, or opening chat with no concrete task, do not invoke this skill.
- One OpenClaw session maps to one SQLBot ask-data chat.
- Always call the OpenClaw `session_status` tool first and read `details.sessionKey`.
- Every session-scoped `sqlbot_skills.py` command must include:
  - `--openclaw-session-key "<sessionKey>"`
  - `--openclaw-agent-id "<agentId>"`
- Never use implicit `default` scope for production user traffic.
- The first ask in a session must know the datasource:
  - either the user already switched datasource in this session
  - or you pass `--datasource`
- If no session datasource is bound yet, the skill may auto-bind the configured default workspace/datasource from `.env`.
- Follow-up asks in the same OpenClaw session should usually call plain `ask "<question>"` and let the skill reuse the bound SQLBot `chat_id`.
- If the user changes workspace or datasource, the skill resets the SQLBot chat binding automatically.
- If the user says to start over, use `session reset`. Use `session reset --full` only when you should also clear workspace and datasource selection.

## Preferred commands

- First resolve `<scriptPath>`, `<sessionKey>`, and `<agentId>` as described above, then reuse them in every shell command below.

- Show current SQLBot binding for this OpenClaw session:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" session show
```

- Reset the SQLBot chat binding for this OpenClaw session:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" session reset
```

- Fully clear workspace and datasource too:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" session reset --full
```

- List workspaces:

```bash
python3 <scriptPath> workspace list
```

- Bind workspace for the current OpenClaw session:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" workspace switch "<workspace>"
```

- List datasources:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" datasource list --workspace "<workspace>"
```

- Bind datasource for the current OpenClaw session:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" datasource switch "<datasource>" --workspace "<workspace>"
```

- First ask with explicit datasource:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" ask "<question>" --datasource "<datasource>" --workspace "<workspace>"
```

- Follow-up ask in the same OpenClaw session:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" ask "<question>"
```

- Force a brand-new SQLBot chat while staying in the same OpenClaw session:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" ask --new-chat "<question>"
```

- List dashboards:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" dashboard list --workspace "<workspace>"
```

- Show dashboard detail:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" dashboard show "<dashboard-id>" --workspace "<workspace>"
```

- Export dashboard:

```bash
python3 <scriptPath> --openclaw-session-key "<sessionKey>" --openclaw-agent-id "<agentId>" dashboard export "<dashboard-id>" --workspace "<workspace>" --output "./dashboard.jpg"
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
- In SQLBot-enabled production agents, use SQLBot for every concrete non-greeting request; do not require an explicit trigger prefix.
- Skip this skill only for pure greeting/help/capability turns or opening chats that contain no concrete request.
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
- Structured trace is **enabled by default** (writes JSONL to `monitoring/sqlbot-events.jsonl` in the skill directory):
  - `--no-emit-trace`: disable trace emission
  - `--trace-id <id>`: override trace ID
  - `--trace-file <path>`: write trace events to a custom file path
  These flags are optional and intended for observability. Do not require them for normal production use.

## Additional resources

- Detailed runtime notes and command reference: [reference.md](reference.md)
- Upstream repository overview: [README.md](README.md)
