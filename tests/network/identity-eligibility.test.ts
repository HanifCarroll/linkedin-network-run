import { describe, expect, test } from "bun:test";
import { resolveOrCreatePerson } from "../../src/network/identity.ts";
import { NOW, setup, walkSend } from "./helpers.ts";

describe("identity and eligibility", () => {
  test("never merges people by name", () => {
    const { database } = setup();
    const first = resolveOrCreatePerson(
      database,
      { name: "Same Name", salesNavId: "sales-1" },
      NOW,
      "p1",
    );
    const second = resolveOrCreatePerson(
      database,
      { name: "Same Name", publicUrl: "https://linkedin.com/in/example/" },
      NOW,
      "p2",
    );
    expect(first.id).not.toBe(second.id);
    database.close();
  });

  test("requires exact source-row evidence before adding an identity alias", () => {
    const { database } = setup();
    resolveOrCreatePerson(
      database,
      { name: "Alias Person", salesNavId: "sales-alias" },
      NOW,
      "alias-person",
    );
    expect(() =>
      resolveOrCreatePerson(
        database,
        {
          name: "Alias Person",
          publicUrl: "https://www.linkedin.com/in/alias-person",
          salesNavId: "sales-alias",
        },
        NOW,
      ),
    ).toThrow("exact source observation evidence");

    // Insert exact observation evidence required by alias rules.
    database
      .query(
        `INSERT INTO source_observations
         (id, invocation_id, source_id, person_id, observed_name, observation_kind,
          row_state, observed_at, run_id, identity_evidence_json)
         VALUES (?, ?, ?, ?, ?, 'candidate', 'connectable', ?, ?, ?)`,
      )
      .run(
        "alias-observation-2",
        "alias-capture-2",
        "hubspot-agency-ops",
        "alias-person",
        "Alias Person",
        NOW,
        null,
        JSON.stringify({
          name: "Alias Person",
          publicUrl: "https://www.linkedin.com/in/alias-person",
          salesNavId: "sales-alias",
        }),
      );
    const updated = resolveOrCreatePerson(
      database,
      {
        name: "Alias Person",
        publicUrl: "https://www.linkedin.com/in/alias-person",
        salesNavId: "sales-alias",
      },
      NOW,
      "unused",
      {
        observationId: "alias-observation-2",
        invocationId: "alias-capture-2",
        sourceId: "hubspot-agency-ops",
      },
    );
    expect(updated.id).toBe("alias-person");
    const alias = database
      .query<{ evidence: string }, [string]>(
        "SELECT evidence FROM person_aliases WHERE person_id = ?",
      )
      .get("alias-person");
    expect(JSON.parse(alias?.evidence ?? "{}")).toEqual({
      anchorKind: "sales_nav_id",
      anchorValue: "sales-alias",
      invocationId: "alias-capture-2",
      observationId: "alias-observation-2",
      sourceId: "hubspot-agency-ops",
    });
    database.close();
  });

  test("active walk attempt blocks a second walk for the same person", () => {
    const { database, engine, runId } = setup();
    const first = walkSend(engine, runId, "hubspot-agency-ops", "shared-lead", "Shared Person");
    const second = engine.recordWalkSends(
      runId,
      "hubspot-b2b-revops",
      {
        sent: [
          {
            rowIdentity: "urn:li:fs_salesProfile:shared-lead",
            name: "Shared Person",
          },
        ],
        skipped: [],
      },
      NOW,
    );
    expect(second.sent).toBe(0);
    expect(engine.projection(runId).provisional).toBe(1);
    expect(
      database
        .query<{ count: number }, [string]>(
          "SELECT COUNT(*) AS count FROM send_attempts WHERE run_id = ?",
        )
        .get(runId)?.count,
    ).toBe(1);
    expect(
      database
        .query<{ id: string }, [string]>("SELECT id FROM send_attempts WHERE id = ?")
        .get(first)?.id,
    ).toBe(first);
    database.close();
  });

  test.each(["pending", "connected", "do_not_contact", "cross_workflow_message_sent"] as const)(
    "relationship fact %s does not block walk send (walk path is explicit)",
    (kind) => {
      const { database, engine, runId } = setup();
      const person = resolveOrCreatePerson(
        database,
        { name: "Suppressed", salesNavId: `suppressed-${kind}` },
        NOW,
        `person-${kind}`,
      );
      engine.addRelationshipFact({
        id: `fact-${kind}`,
        personId: person.id,
        kind,
        effectiveAt: NOW,
        evidence: {},
      });
      // Walk path records explicit operator sends; relationship facts do not auto-filter
      // sent rows (skips are explicit via the skipped array).
      const attemptId = walkSend(
        engine,
        runId,
        "hubspot-agency-ops",
        `suppressed-${kind}`,
        "Suppressed",
      );
      expect(typeof attemptId).toBe("string");
      expect(engine.projection(runId).provisional).toBe(1);
      database.close();
    },
  );

  test("walk skipped already_pending records proven_no_send fact without consuming capacity", () => {
    const { database, engine, runId } = setup();
    const result = engine.recordWalkSends(
      runId,
      "hubspot-agency-ops",
      {
        sent: [],
        skipped: [
          {
            rowIdentity: "urn:li:fs_salesProfile:skip-pending",
            name: "Skip Pending",
            reason: "already_pending",
          },
        ],
      },
      NOW,
    );
    expect(result).toEqual({ sent: 0, skipped: 1 });
    expect(engine.projection(runId).remainingCapacity).toBe(30);
    expect(
      database
        .query<{ kind: string }, []>(
          "SELECT kind FROM relationship_facts WHERE id LIKE 'walk-skip:%'",
        )
        .get()?.kind,
    ).toBe("proven_no_send");
    database.close();
  });
});
