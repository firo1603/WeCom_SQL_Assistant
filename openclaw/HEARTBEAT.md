# Corp Assistant Heartbeat

Use heartbeat for local operational memory maintenance only.

If nothing below needs follow-up after generation, reply exactly:

`HEARTBEAT_OK`

## Rules

- Production stability first
- Keep work local to the `corp-assistant` workspace
- Never modify SQLBot skill source, config, prompts, or state from heartbeat
- The only permitted SQLBot workspace write from heartbeat is deleting artifact run directories older than 30 days
- Never modify global Gateway config from heartbeat
- Never write raw WeCom text, sender ids, session keys, SQL, result rows, artifact paths, telemetry trace ids, or business values into memory files
- Keep hot questions as taxonomy labels plus aggregate counts only
- Treat `DREAMS.md` as review output, not as the source of durable truth

## Checklist

### 1. Refresh operational memory

Run:

```bash
python3 scripts/maintain_operational_memory.py
```

This maintenance entrypoint refreshes sanitized operational memory, runs the sanitizer, and explicitly reindexes `corp-assistant` memory search.

It reads:

- `~/.openclaw/agents/corp-assistant/sessions/*.jsonl`
- `skills/sqlbot-workspace-dashboard/monitoring/sqlbot-events.jsonl`

It also enforces a 30-day retention window for SQLBot artifact run directories.

It rewrites:

- `memory/runtime.md`
- `memory/hot-topics.md`
- `memory/incidents.md`
- `memory/YYYY-MM-DD.md` for today
- `memory/heartbeat-state.json`

It then runs:

```bash
openclaw memory index --agent corp-assistant
```

### 2. Review generated summaries

Read these files after refresh:

- `MEMORY.md`
- `memory/runtime.md`
- `memory/hot-topics.md`
- `memory/incidents.md`
- `memory/topic-taxonomy.md`
- today's `memory/YYYY-MM-DD.md`

### 3. Promote only stable patterns

Only update `MEMORY.md` when a pattern is clearly durable:

- a topic remains high in both 7-day and 30-day windows
- an error pattern repeats and has a reusable mitigation
- a usage pattern changes how the production agent should be operated

When promoting:

- keep the note short
- keep the note sanitized
- prefer labels, rates, and operating rules over examples

### 4. Prefer no-op over speculation

If generation succeeded and there is no clear durable promotion, do nothing else and reply `HEARTBEAT_OK`.
