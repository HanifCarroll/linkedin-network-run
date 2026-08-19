import { readFileSync } from "node:fs";
import { join } from "node:path";
import { openDatabase } from "../db/database.ts";
import { type SalesNavInput, SalesNavStore } from "../salesnav.ts";
import { SalesNavAccountStore } from "../salesnav-account.ts";

export async function salesnav(input: SalesNavInput): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const store = new SalesNavStore(opened.database),
      account = new SalesNavAccountStore(opened.database);
    const now = new Date().toISOString(),
      prefix = `salesnav ${input.lane ?? "staffing"}`;
    const readBody = () =>
      input.payloadPath === "-"
        ? readFileSync(0, "utf8")
        : readFileSync(input.payloadPath ?? "", "utf8");
    if (input.command.startsWith("account-") || input.command === "firm-research-record") {
      switch (input.command) {
        case "account-capture-start":
          return { command: `${prefix} ${input.command}`, run: account.start(input, now) };
        case "account-capture-ingest":
          return {
            command: `${prefix} ${input.command}`,
            ...account.ingest(input, readBody(), now),
          };
        case "account-capture-finish":
          return { command: `${prefix} ${input.command}`, run: account.finish(input, now) };
        case "account-normalize":
          return { command: `${prefix} ${input.command}`, ...account.normalize(input, now) };
        case "account-status":
          return { command: `${prefix} ${input.command}`, ...account.status(input.runId) };
        case "account-qualify-next": {
          const next = account.next(input);
          return {
            command: `${prefix} ${input.command}`,
            found: next !== null,
            ...(next === null ? {} : { account: next }),
          };
        }
        case "account-qualify-record":
          return { command: `${prefix} ${input.command}`, ...account.record(input, now) };
        case "account-people-candidates":
          return { command: `${prefix} ${input.command}`, ...account.peopleCandidates(input) };
        case "firm-research-record":
          return { command: `${prefix} ${input.command}`, ...account.firmResearch(input, now) };
      }
    }
    switch (input.command) {
      case "capture-start":
        return { command: `${prefix} ${input.command}`, run: store.start(input, now) };
      case "capture-ingest":
        return { command: `${prefix} ${input.command}`, ...store.ingest(input, readBody(), now) };
      case "capture-finish":
        return { command: `${prefix} ${input.command}`, run: store.finish(input, now) };
      case "normalize":
        return { command: `${prefix} ${input.command}`, ...store.normalize(input, now) };
      case "qualify":
        return { command: `${prefix} ${input.command}`, ...store.qualify(input, now) };
      case "status":
        return { command: `${prefix} ${input.command}`, ...store.status(input.runId) };
    }
  } finally {
    opened.database.close();
  }
}
