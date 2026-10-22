// Captures errors briefly for server-side correlation without writing their
// messages, request data, or stacks to logs.

let lastCapturedError: { error: unknown; at: number } | undefined;
const TTL_MS = 5_000;

function record(error: unknown) {
  lastCapturedError = { error, at: Date.now() };
}

function isErrorLike(value: unknown): value is Error {
  return value instanceof Error;
}

// Wrap console.error so Error objects are captured for correlation but their
// message and stack are replaced before reaching the host log pipeline.
const originalConsoleError = console.error.bind(console);
console.error = (...args: unknown[]) => {
  const redacted = args.map((arg) => {
    if (!isErrorLike(arg)) return arg;
    record(arg);
    return `${arg.name || "Error"}: details redacted`;
  });
  originalConsoleError(...redacted);
};

if (typeof globalThis.addEventListener === "function") {
  globalThis.addEventListener("error", (event) => record((event as ErrorEvent).error ?? event));
  globalThis.addEventListener("unhandledrejection", (event) =>
    record((event as PromiseRejectionEvent).reason),
  );
}

export function consumeLastCapturedError(): unknown {
  if (!lastCapturedError) return undefined;
  if (Date.now() - lastCapturedError.at > TTL_MS) {
    lastCapturedError = undefined;
    return undefined;
  }
  const { error } = lastCapturedError;
  lastCapturedError = undefined;
  return error;
}
