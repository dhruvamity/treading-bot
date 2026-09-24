# treading-bot

A maker bot for Arcus perps. The code, how it works and the commands are in [bot/README.md](bot/README.md).

```
bot/                        the bot: scout (record + backtest + rank), pilot (approve + run), live engine, Telegram
arclight-agent-prompts.md   the original build spec (old name kept)
arclight-project-spec       the original project spec
lighter-rh-docs/            a copy of the Lighter (Robinhood Chain) API docs used while building
```

Credentials go in `bot/.env` (see `bot/.env.example`); it is never committed.
