# Telegram Cargo Bot

A Python Telegram bot foundation for cargo workflows, built incrementally as the user requests each feature.

## Run & Operate

- `uv run python -m cargo_bot` — run the Telegram bot
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required environment: `TELEGRAM_BOT_TOKEN` and `CUSTOMER_DATA_ENCRYPTION_KEY` in Replit Secrets, plus the managed PostgreSQL `DATABASE_URL`

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
- Customer phone, passport number, and passport photo are encrypted with AES-GCM before being stored in PostgreSQL; the Telegram user lookup key is HMAC-derived.
- The bot uses the managed PostgreSQL connection as provided by Replit and does not override its connection-security settings.

## Product

The bot supports private customer registration: it collects the customer's own Telegram contact, validates the passport number format, accepts a JPEG/PNG passport image, and stores the submitted data encrypted in PostgreSQL. No other cargo features are included.

## User preferences

- Build the bot step by step; do not add features until the user instructs you to.

## Gotchas

- Never print the Telegram token or database connection string in logs.
- Never log contact details, passport numbers, passport images, or encryption keys. Losing `CUSTOMER_DATA_ENCRYPTION_KEY` makes stored registrations unreadable.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
