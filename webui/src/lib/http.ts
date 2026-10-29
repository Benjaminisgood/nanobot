import { preparePortalRequest, validatePortalResponse } from "./portal-context";

const DEFAULT_HTTP_TIMEOUT_MS = 20_000;

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_HTTP_TIMEOUT_MS,
): Promise<Response> {
  const prepared = preparePortalRequest(input, init);
  init = prepared.init;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    return validatePortalResponse(await fetch(input, init), prepared.portal, prepared.bootstrap);
  }

  const controller = typeof AbortController !== "undefined"
    ? new AbortController()
    : null;
  const externalSignal = init.signal;
  const abortFromExternal = () => controller?.abort();
  let timeoutId: ReturnType<typeof setTimeout> | undefined;

  if (controller && externalSignal) {
    if (externalSignal.aborted) {
      controller.abort();
    } else {
      externalSignal.addEventListener("abort", abortFromExternal, { once: true });
    }
  }

  try {
    const request = fetch(input, {
      ...init,
      signal: controller?.signal ?? externalSignal,
    });
    const timeout = new Promise<Response>((_, reject) => {
      timeoutId = setTimeout(() => {
        reject(new Error(`Request timed out after ${timeoutMs}ms`));
        controller?.abort();
      }, timeoutMs);
    });
    return validatePortalResponse(await Promise.race([request, timeout]), prepared.portal, prepared.bootstrap);
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", abortFromExternal);
  }
}
