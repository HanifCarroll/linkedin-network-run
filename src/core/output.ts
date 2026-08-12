import type { ErrorEnvelope, SuccessEnvelope } from "./envelope.ts";

export type OutputMode = "human" | "json";

export function writeSuccess<T>(envelope: SuccessEnvelope<T>, mode: OutputMode): void {
  if (mode === "json") {
    console.log(JSON.stringify(envelope));
    return;
  }
  console.log(JSON.stringify(envelope.data, null, 2));
}

export function writeError(envelope: ErrorEnvelope, mode: OutputMode): void {
  if (mode === "json") {
    console.log(JSON.stringify(envelope));
    return;
  }
  console.error(`Error [${envelope.error.code}]: ${envelope.error.message}`);
}
