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
