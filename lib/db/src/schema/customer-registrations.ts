import type { Buffer } from "node:buffer";
import {
  bigint,
  customType,
  index,
  integer,
  pgTable,
  serial,
  timestamp,
  varchar,
} from "drizzle-orm/pg-core";

const bytea = customType<{ data: Buffer; driverData: Buffer }>({
  dataType: () => "bytea",
});

export const customerRegistrationsTable = pgTable("customer_registrations", {
  userKey: bytea("user_key").primaryKey(),
  encryptedPayload: bytea("encrypted_payload").notNull(),
  trackingCode: varchar("tracking_code", { length: 32 }).unique(),
  phoneLookupKey: bytea("phone_lookup_key"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
}, (table) => [index("customer_registrations_phone_lookup_idx").on(table.phoneLookupKey)]);

export const customerTrackingCodeCountersTable = pgTable(
  "customer_tracking_code_counters",
  {
    id: integer("id").primaryKey(),
    lastNumber: bigint("last_number", { mode: "number" }).notNull(),
  },
);

export const customerTrackingCodeAuditTable = pgTable(
  "customer_tracking_code_audit",
  {
    id: serial("id").primaryKey(),
    userKey: bytea("user_key")
      .notNull()
      .references(() => customerRegistrationsTable.userKey, {
        onDelete: "cascade",
      }),
    oldTrackingCode: varchar("old_tracking_code", { length: 32 }),
    newTrackingCode: varchar("new_tracking_code", { length: 32 })
      .notNull()
      .unique(),
    changedByTelegramId: bigint("changed_by_telegram_id", { mode: "number" }),
    actorType: varchar("actor_type", { length: 16 }).notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
);

export const customerLanguagePreferencesTable = pgTable(
  "customer_language_preferences",
  {
    userKey: bytea("user_key").primaryKey(),
    languageCode: varchar("language_code", { length: 2 }).notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
);
