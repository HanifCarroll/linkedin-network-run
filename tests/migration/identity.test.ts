import { describe, expect, test } from "bun:test";
import { buildIdentity, canonicalPublicUrl } from "../../src/migration/identity.ts";

describe("migration identity", () => {
  test("uses Sales Navigator identity before public URL and lead key", () => {
    expect(
      buildIdentity({
        profileUrl: "https://www.linkedin.com/sales/lead/SALES123,NAME",
        publicProfileUrl: "https://linkedin.com/in/Example/",
        leadKey: "legacy",
      }),
    ).toEqual({
      canonicalKey: "sales:SALES123",
      salesNavigatorId: "SALES123",
      publicProfileUrl: "https://www.linkedin.com/in/example",
      leadKey: "legacy",
    });
  });

  test("accepts canonical www LinkedIn public profiles", () => {
    expect(canonicalPublicUrl("https://www.linkedin.com/in/Example/")).toBe(
      "https://www.linkedin.com/in/example",
    );
  });

  test("never creates an identity from a name", () => {
    expect(buildIdentity({})).toBeNull();
  });

  test("rejects non-profile public URLs", () => {
    expect(canonicalPublicUrl("https://example.com/in/person")).toBeNull();
  });
});
