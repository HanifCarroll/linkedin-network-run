import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export type JsonObject = Record<string, unknown>;

function readObject(path: string): JsonObject {
  const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`Expected JSON object: ${path}`);
  }
  return parsed as JsonObject;
}

export function readLegacyJson(root: string): {
  leadLedger: JsonObject;
  active: JsonObject;
  parked: Array<{ path: string; value: JsonObject }>;
} {
  const parkedRoot = join(root, "parked-network-runs");
  const parked = readdirSync(parkedRoot)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => ({ path: join(parkedRoot, name), value: readObject(join(parkedRoot, name)) }));
  return {
    leadLedger: readObject(join(root, "lead-ledger.json")),
    active: readObject(join(root, "active.json")),
    parked,
  };
}

export function objectArray(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonObject =>
        Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
}

export function objectMap(value: unknown): Array<[string, JsonObject]> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value).filter((entry): entry is [string, JsonObject] =>
    Boolean(entry[1] && typeof entry[1] === "object" && !Array.isArray(entry[1])),
  );
}
