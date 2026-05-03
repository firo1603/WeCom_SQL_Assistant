# SQLBot Skill Reference

## Runtime model

This skill now stores state by OpenClaw session, not as one global datasource pointer.

Each OpenClaw session keeps:

- bound workspace
- bound datasource
- bound SQLBot `chat_id`
- last SQLBot `record_id`
- last generated artifacts

That means:

- follow-up asks in the same OpenClaw session reuse the same SQLBot chat
- switching workspace or datasource resets the SQLBot chat binding
- `session reset` clears the SQLBot chat binding for the current OpenClaw session

## Configuration

Create `.env` next to `SKILL.md`:

```bash
cp .env.example .env
```

Required fields:

```bash
SQLBOT_BASE_URL=https://your-host/api/v1
SQLBOT_API_KEY_ACCESS_KEY=your-access-key
SQLBOT_API_KEY_SECRET_KEY=your-secret-key
```

Optional fields:

```bash
SQLBOT_API_KEY_TTL_SECONDS=300
SQLBOT_TIMEOUT=30
SQLBOT_BROWSER_PATH=/path/to/chrome
SQLBOT_STATE_FILE=/absolute/path/to/.sqlbot-skill-state.json
```

Notes:

- `SQLBOT_BASE_URL` may be either the app root or the API root; the script normalizes it to `/api/v1`.
- Authentication uses `X-SQLBOT-ASK-TOKEN: sk <signed-jwt>`.
- If `SQLBOT_STATE_FILE` is not set, the skill defaults to `.sqlbot-skill-state.json` next to `sqlbot_skills.py` in the installed skill directory.
- Ask artifacts are written to `artifacts/` next to the state file.
- Structured trace defaults to `monitoring/sqlbot-events.jsonl` in the installed skill directory unless `--trace-file` is set.

## Command reference

Show current bound session state:

```bash
python3 sqlbot_skills.py session show
```

Reset current SQLBot chat, keep workspace and datasource:

```bash
python3 sqlbot_skills.py session reset
```

Fully clear the current session binding:

```bash
python3 sqlbot_skills.py session reset --full
```

List workspaces:

```bash
python3 sqlbot_skills.py workspace list
```

Switch workspace:

```bash
python3 sqlbot_skills.py workspace switch 1
python3 sqlbot_skills.py workspace switch "默认工作空间"
```

List datasources:

```bash
python3 sqlbot_skills.py datasource list
python3 sqlbot_skills.py datasource list --workspace 1
```

Switch datasource for the current OpenClaw session:

```bash
python3 sqlbot_skills.py datasource switch 3
python3 sqlbot_skills.py datasource switch "水果通数据库" --workspace 1
```

Show current datasource:

```bash
python3 sqlbot_skills.py datasource current
```

First ask with explicit datasource:

```bash
python3 sqlbot_skills.py ask "本周销售额是多少" --workspace 1 --datasource 3
```

Follow-up ask in the same OpenClaw session:

```bash
python3 sqlbot_skills.py ask "继续按地区拆分"
```

Force a new SQLBot chat inside the same OpenClaw session:

```bash
python3 sqlbot_skills.py ask --new-chat "重新从利润角度分析"
```

Return full raw SQLBot payload too:

```bash
python3 sqlbot_skills.py ask "本周销售额是多少" --include-raw
```

Include raw SSE events:

```bash
python3 sqlbot_skills.py ask "本周销售额是多少" --include-raw --include-events
```

List dashboards:

```bash
python3 sqlbot_skills.py dashboard list --workspace 1
```

Show dashboard detail:

```bash
python3 sqlbot_skills.py dashboard show <dashboard-id> --workspace 1
```

Export dashboard:

```bash
python3 sqlbot_skills.py dashboard export <dashboard-id> --workspace 1 --format png --output ./dashboard.png
```

## Output layout

The compact `ask` response contains:

- `scope`
- `session`
- `summary`
- `artifacts`
- `source`

The full raw SQLBot response is written to `artifacts.raw_json`.

The normalized structure is written to `artifacts.normalized_json`.

When data rows exist, the skill also writes:

- `artifacts.data_csv`
- `artifacts.chart_png`

## Chart behavior

The skill first tries to parse SQLBot's `chart` payload.

Fallback rules:

- SQLBot chart type `table` -> render a table image
- temporal category + numeric series -> line chart
- categorical dimension + numeric series -> bar chart
- no usable chart structure -> render a table image

The renderer is local and does not depend on dashboard export.

## Explicit OpenClaw binding

For manual tests outside a normal OpenClaw skill invocation, you can pass:

```bash
python3 sqlbot_skills.py \
  --openclaw-session-key agent:main:telegram:direct:test-user \
  --openclaw-session-id sqlbot-test \
  ask "本周销售额是多少" --workspace 1 --datasource 3
```

If `--openclaw-session-id` is omitted, the skill tries to resolve it from:

- `OPENCLAW_MCP_SESSION_KEY`
- `OPENCLAW_MCP_AGENT_ID`
- `~/.openclaw/agents/<agentId>/sessions/sessions.json`
