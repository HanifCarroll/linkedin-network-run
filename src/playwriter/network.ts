import type { PlaywriterClient } from "./client.ts";
import { compileNetworkScript, type NetworkScriptInput } from "./scripts.ts";
import type {
  CandidateIdentity,
  InvocationResult,
  NetworkCommand,
  PreparedSendInvocation,
  SendPreparationReceipt,
} from "./types.ts";
import {
  assertNetworkCommand,
  assertSendPreparationBinding,
  immutableSendPreparationReceipt,
} from "./validation.ts";

export async function invokeNetworkStep(
  client: PlaywriterClient,
  command: NetworkCommand,
  sessionId: number,
  input: NetworkScriptInput = {},
): Promise<InvocationResult> {
  assertNetworkCommand(command);
  if (["click-send", "prepare-send", "commit-send"].includes(command))
    throw new TypeError("Send commands require prepareNetworkSend or commitNetworkSend");
  const descriptor = compileNetworkScript(command, input);
  return client.invoke({
    sessionId,
    descriptor,
    ...(input.candidate ? { candidate: input.candidate } : {}),
    input: {
      ...(input.url ? { url: input.url } : {}),
    },
  });
}

export async function prepareNetworkSend(
  client: PlaywriterClient,
  sessionId: number,
  input: { readonly attemptId: string; readonly candidate: CandidateIdentity },
): Promise<PreparedSendInvocation> {
  const descriptor = compileNetworkScript("prepare-send", input);
  const invocation = await client.invoke({
    sessionId,
    descriptor,
    candidate: input.candidate,
    input: { attemptId: input.attemptId },
  });
  if (invocation.receipt.outcome !== "succeeded" || invocation.receipt.result === null)
    return Object.freeze({ invocation, receipt: null });
  const receipt = immutableSendPreparationReceipt(invocation.receipt.result.data);
  assertSendPreparationBinding(receipt, sessionId);
  return Object.freeze({ invocation, receipt });
}

export async function commitNetworkSend(
  client: PlaywriterClient,
  sessionId: number,
  preparation: SendPreparationReceipt,
): Promise<InvocationResult> {
  assertSendPreparationBinding(preparation, sessionId);
  const receipt = immutableSendPreparationReceipt(preparation);
  const descriptor = compileNetworkScript("commit-send", {
    candidate: receipt.candidate,
    sendPreparation: receipt,
  });
  return client.invoke({
    sessionId,
    descriptor,
    candidate: receipt.candidate,
    sendPreparation: receipt,
    input: { sendPreparation: receipt },
  });
}
