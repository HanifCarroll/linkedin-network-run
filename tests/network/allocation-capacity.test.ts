import { describe, expect, test } from "bun:test";
import { resolveOrCreatePerson } from "../../src/network/identity.ts";
import { NOW, recordBaseline, setup, walkRows } from "./helpers.ts";

describe("allocation and capacity under walk sends", () => {
  test("preferred 15/15 is tracked per source after walk sends", () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    engine.recordWalkSends(
      runId,
      "hubspot-agency-ops",
      { sent: walkRows("agency", 8), skipped: [] },
      NOW,
    );
    engine.recordWalkSends(
      runId,
      "hubspot-b2b-revops",
      { sent: walkRows("coo", 7), skipped: [] },
      NOW,
    );
    expect(engine.projection(runId)).toMatchObject({
      provisional: 15,
      remainingCapacity: 15,
      bySource: {
        "hubspot-agency-ops": { possible: 8 },
        "hubspot-b2b-revops": { possible: 7 },
      },
    });
    database.close();
  });

  test("shortfall on one source may be filled by the other via walk budget", () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    engine.recordWalkSends(
      runId,
      "hubspot-agency-ops",
      { sent: walkRows("agency", 7), skipped: [] },
      NOW,
    );
    engine.recordWalkSends(
      runId,
      "hubspot-b2b-revops",
      { sent: walkRows("coo", 23), skipped: [] },
      NOW,
    );
    expect(engine.projection(runId)).toMatchObject({
      provisional: 30,
      remainingCapacity: 0,
      bySource: {
        "hubspot-agency-ops": { possible: 7 },
        "hubspot-b2b-revops": { possible: 23 },
      },
    });
    database.close();
  });

  test("hard caps walk sends at 30 remaining capacity", () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    engine.recordWalkSends(
      runId,
      "hubspot-agency-ops",
      { sent: walkRows("agency", 20), skipped: [] },
      NOW,
    );
    const second = engine.recordWalkSends(
      runId,
      "hubspot-b2b-revops",
      { sent: walkRows("coo", 20), skipped: [] },
      NOW,
    );
    expect(second.sent).toBe(10);
    expect(engine.projection(runId).provisional).toBe(30);
    expect(engine.projection(runId).remainingCapacity).toBe(0);
    database.close();
  });

  test("older unresolved relationship fact does not consume capacity", () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    const person = resolveOrCreatePerson(
      database,
      { name: "Legacy unresolved", salesNavId: "legacy-unresolved" },
      NOW,
      "legacy-person",
    );
    engine.addRelationshipFact({
      id: "legacy-fact",
      personId: person.id,
      kind: "unresolved_send",
      effectiveAt: "2026-08-02T00:00:00Z",
      evidence: {},
    });
    expect(engine.projection(runId).remainingCapacity).toBe(30);
    database.close();
  });
});
