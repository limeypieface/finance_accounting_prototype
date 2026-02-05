# Logs

| File | Written by | When |
|------|------------|------|
| **interactive.log** | Interactive CLI | Only when you run `python3 scripts/interactive.py`. Structured JSON lines (one per record). Flushed after each write so you can `tail -f` while using the CLI. |

If `interactive.log` is empty, you haven’t run the interactive CLI yet, or it exited before logging (e.g. database connection failed). Run from project root:

```bash
python3 scripts/interactive.py
```

You should see a startup line in `interactive.log` and “Logging to: …/logs/interactive.log” on stderr.
