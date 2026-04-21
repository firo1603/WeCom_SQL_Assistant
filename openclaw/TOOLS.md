# Tool Policy Notes

Current production capability is intentionally narrow.

Use tools only as needed to complete the SQLBot workflow.

Allowed production intent:

- query internal data through the SQLBot skill
- continue the same analysis thread for the same user
- reset the current SQLBot session when the user asks to start over

Do not broaden into:

- generic coding assistance
- web research
- unrelated internal automation
- personal assistant behavior

If a request falls outside current production scope, say so directly.
