import type { Buffer } from "node:buffer";
import { customType, pgTable, timestamp } from "drizzle-orm/pg-core";

const bytea = customType<{ data: Buffer; driverData: Buffer }>({
  dataType: () => "bytea",
});

export const customerRegistrationsTable = pgTable("customer_registrations", {
  userKey: bytea("user_key").primaryKey(),
  encryptedPayload: bytea("encrypted_payload").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});
