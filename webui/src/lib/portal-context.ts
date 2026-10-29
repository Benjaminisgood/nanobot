/** Identity of this HTML document, supplied only by its same-origin portal gateway. */
const APP_ID = "nanobot";
export const PORTAL_AUTH_REQUIRED_EVENT = "portal-auth-required";

export interface PortalContext {
  instanceId: string;
  portalUrl: string;
  app: typeof APP_ID;
}

export class PortalAccessError extends Error {
  constructor(readonly reason: "expired" | "instance") {
    super(reason === "expired" ? "Portal sign-in required" : "Portal instance changed; reopen this app from the portal");
    this.name = "PortalAccessError";
  }
}

export function readPortalContext(): PortalContext | null {
  if (typeof document === "undefined" || typeof window === "undefined") return null;
  const names = ["portal-instance-id", "portal-url", "portal-app"];
  const tags = names.map((name) => document.head.querySelectorAll<HTMLMetaElement>(`meta[name="${name}"]`));
  if (tags.every((matches) => matches.length === 0)) return null;
  if (tags.some((matches) => matches.length !== 1)) throw new Error("Incomplete portal metadata");
  const [instanceId, rawUrl, app] = tags.map((matches) => matches[0].content.trim());
  if (app !== APP_ID || !instanceId.startsWith(`${app}:`) || instanceId.length <= app.length + 1
    || instanceId.length > 256 || Array.from(instanceId).some((char) => char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127)) {
    throw new Error("Invalid portal instance metadata");
  }
  const portalUrl = new URL(rawUrl);
  if (!["http:", "https:"].includes(portalUrl.protocol) || portalUrl.username || portalUrl.password) {
    throw new Error("Invalid portal URL");
  }
  return { instanceId, portalUrl: portalUrl.href, app };
}

function denyPortalAccess(reason: "expired" | "instance"): never {
  window.dispatchEvent(new Event(PORTAL_AUTH_REQUIRED_EVENT));
  throw new PortalAccessError(reason);
}

/** Bind every gateway request to this document without forwarding credentials elsewhere. */
export function preparePortalRequest(input: RequestInfo | URL, init: RequestInit): {
  init: RequestInit;
  portal: PortalContext | null;
  bootstrap: boolean;
} {
  const portal = readPortalContext();
  if (!portal) return { init, portal, bootstrap: false };
  const target = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const url = new URL(target, window.location.href);
  if (url.origin !== window.location.origin || url.username || url.password) {
    throw new Error("Portal requests must stay on this app's origin");
  }
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init.headers).forEach((value, name) => headers.set(name, value));
  const bootstrap = url.pathname === "/webui/bootstrap" || url.pathname === "/webui/terminal";
  if (bootstrap) {
    headers.delete("Authorization");
    headers.delete("X-Nanobot-Auth");
  }
  headers.set("X-Portal-Expected-Instance", portal.instanceId);
  return { init: { ...init, headers, credentials: "same-origin", redirect: "error" }, portal, bootstrap };
}

export function validatePortalResponse(response: Response, portal: PortalContext | null, bootstrap: boolean): Response {
  if (!portal) return response;
  const instanceId = response.headers.get("X-Portal-Instance-ID");
  if (response.status === 401 || (response.status === 403 && (bootstrap || !instanceId))) denyPortalAccess("expired");
  if (instanceId !== portal.instanceId || (bootstrap && response.status === 409)) denyPortalAccess("instance");
  return response;
}

export function portalWebSocketUrl(wsPath: string, token: string | null | undefined, wsUrl?: string | null): string | null {
  const portal = readPortalContext();
  if (!portal) return null;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const path = wsPath.startsWith("/") ? wsPath : `/${wsPath}`;
  const url = new URL(wsUrl || `${protocol}//${window.location.host}${path}`);
  if (url.protocol !== protocol || url.host !== window.location.host || url.username || url.password) {
    throw new Error("Portal WebSockets must stay on this app's origin");
  }
  if (token) url.searchParams.set("token", token);
  else url.searchParams.delete("token");
  url.searchParams.set("portal_instance", portal.instanceId);
  return url.href;
}
