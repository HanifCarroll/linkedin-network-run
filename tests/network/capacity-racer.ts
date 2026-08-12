import { existsSync, writeFileSync } from "node:fs";
import { openDatabase } from "../../src/db/database.ts";
import { NetworkEngine } from "../../src/network/engine.ts";

const [databasePath, readyPath, goPath, salesLeadId] = Bun.argv.slice(2);
if (!databasePath || !readyPath || !goPath || !salesLeadId) {
  throw new Error("capacity racer requires database, ready, go, and salesLeadId arguments");
}

const opened = openDatabase(databasePath);
writeFileSync(readyPath, "ready");
while (!existsSync(goPath)) await Bun.sleep(2);

try {
  const result = new NetworkEngine(opened.database).recordWalkSends(
    "run",
    "hubspot-agency-ops",
    {
      sent: [
        {
          rowIdentity: `urn:li:fs_salesProfile:${salesLeadId}`,
          name: `Racer ${salesLeadId}`,
        },
      ],
      skipped: [],
    },
    "2026-08-03T12:30:00Z",
  );
  process.stdout.write(JSON.stringify({ reserved: result.sent === 1 }));
} catch (error) {
  process.stdout.write(
    JSON.stringify({
      error: error instanceof Error ? error.message : String(error),
      reserved: false,
    }),
  );
} finally {
  opened.database.close();
}
