export const DAILY_TARGET = 30;
export const PREFERRED_PER_SOURCE = 15;
// Delay between serial sends inside a batch. LinkedIn throttles the Sales
// Navigator profile endpoint (HTTP 429) under rapid-fire candidate lookups.
export const SEND_PACING_MS = 5_000;

export const SOURCES = [
  {
    id: "hubspot-agency-ops",
    name: "Consulting - HubSpot Agency Ops",
    preferredAllocation: 15,
    reservoirTarget: 30,
    savedSearchId: "1980844577",
  },
  {
    id: "hubspot-b2b-revops",
    name: "Consulting - HubSpot B2B RevOps",
    preferredAllocation: 15,
    reservoirTarget: 30,
    savedSearchId: "1980870185",
  },
] as const;

export type SourceId = (typeof SOURCES)[number]["id"];
