export const DAILY_TARGET = 30;
export const PREFERRED_PER_SOURCE = 15;
// Delay between serial sends inside a batch. LinkedIn throttles the Sales
// Navigator profile endpoint (HTTP 429) under rapid-fire candidate lookups.
export const SEND_PACING_MS = 5_000;

export const SOURCES = [
  {
    id: "b2b-saas-founders",
    name: "B2B SaaS Founders & CTOs",
    preferredAllocation: 15,
    reservoirTarget: 30,
    savedSearchId: "2006164114",
  },
  {
    id: "b2b-saas-engineering-product-leaders",
    name: "B2B SaaS Engineering & Product Leaders",
    preferredAllocation: 15,
    reservoirTarget: 30,
    savedSearchId: "2006164122",
  },
] as const;

export type SourceId = (typeof SOURCES)[number]["id"];
