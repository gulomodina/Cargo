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
- Admin access: `TELEGRAM_ADMIN_IDS` is a comma-separated allowlist of Telegram user IDs; an empty list denies all admin commands

## Stack

- Python 3.11, `python-telegram-bot`, psycopg 3
- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `src/cargo_bot/` — Telegram registration and admin commands, encryption, configuration, and database access
- `lib/db/` — shared PostgreSQL schema for customer registrations, tracking-code audits, and language preferences
- `artifacts/api-server/` — shared Express API

## Architecture decisions

- Telegram's official Bot API is called directly from Python; no Telegram Replit integration is configured.
- Customer phone, passport number, passport photo, and Telegram ID are encrypted with AES-GCM before being stored in PostgreSQL; Telegram and phone lookup keys are HMAC-derived.
- Customer language preferences are stored separately from registration records under an HMAC-derived user key; the bot supports Uzbek and Russian.
- Tracking codes are allocated transactionally from `UZ10001`, protected by database uniqueness constraints, and every assignment or change is recorded in an audit table.
- Admin search, code changes, and history are private-chat-only and require a Telegram user ID in `TELEGRAM_ADMIN_IDS`; code changes notify the customer after the database commit.
- The bot uses the managed PostgreSQL connection as provided by Replit and does not override its connection-security settings.

## Product

The bot supports Uzbek and Russian customer menus, private customer registration, and admin-only customer tracking-code management. Customers can change their language in Settings. It collects the customer's own Telegram contact, validates the passport number format, accepts a JPEG/PNG passport image, and stores the submitted data encrypted in PostgreSQL. No other cargo features are included.

## User preferences

- Build the bot step by step; do not add features until the user instructs you to.

## Gotchas

- Never print the Telegram token or database connection string in logs.
- Never log contact details, passport numbers, passport images, or encryption keys. Losing `CUSTOMER_DATA_ENCRYPTION_KEY` makes stored registrations unreadable.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
