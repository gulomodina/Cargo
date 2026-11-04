# Telegram Cargo Bot

A Python Telegram bot foundation for cargo workflows, built incrementally as the user requests each feature.

## Run & Operate

- `uv run python -m cargo_bot` — run the Telegram bot
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required environment: `TELEGRAM_BOT_TOKEN` in Replit Secrets and the managed PostgreSQL `DATABASE_URL`

## Stack

- Python 3.11, `python-telegram-bot`, psycopg 3
- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `src/cargo_bot/` — Telegram bot configuration, startup, and database connectivity check
- `lib/db/` — shared PostgreSQL schema for the TypeScript API
- `artifacts/api-server/` — shared Express API

## Architecture decisions

- Telegram's official Bot API is called directly from Python; no Telegram Replit integration is configured.
- The bot uses the managed PostgreSQL connection as provided by Replit; it does not override platform connection-security settings or create tables until a cargo feature needs them.

## Product

The current bot foundation validates required environment variables, checks the database connection at startup, and responds to `/start`. Cargo management features are intentionally not included yet.

## User preferences

- Build the bot step by step; do not add features until the user instructs you to.

## Gotchas

- Never print the Telegram token or database connection string in logs.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
