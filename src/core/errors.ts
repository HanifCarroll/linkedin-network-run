export class CliError extends Error {
  readonly code: string;
  readonly details?: Record<string, unknown>;
  readonly exitCode: number;

  constructor(
    code: string,
    message: string,
    options: { details?: Record<string, unknown>; exitCode?: number } = {},
  ) {
    super(message);
    this.name = "CliError";
    this.code = code;
    if (options.details !== undefined) this.details = options.details;
    this.exitCode = options.exitCode ?? 1;
  }
}
