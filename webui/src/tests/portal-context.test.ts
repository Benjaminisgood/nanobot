import { afterEach, describe, expect, it, vi } from "vitest";

import { consumeUrlBootstrapSecret, deriveWsUrl, fetchBootstrap, loadSavedSecret, saveSecret } from "@/lib/bootstrap";
import { fetchWithTimeout } from "@/lib/http";
import { PortalAccessError, readPortalContext } from "@/lib/portal-context";

const instanceId = "nanobot:user-a";
const storageKey = "nanobot-webui.bootstrap-secret";

function portal() {
  for (const [name, content] of Object.entries({
    "portal-instance-id": instanceId,
    "portal-url": "http://localhost:8760/",
    "portal-app": "nanobot",
  })) {
    const tag = document.createElement("meta");
    tag.name = name;
    tag.content = content;
    document.head.append(tag);
  }
}

function response(status = 200, id: string | null = instanceId) {
  return new Response(JSON.stringify({ token: "short-ws", api_token: "short-api", ws_path: "/ws" }), {
    status,
    headers: id === null ? {} : { "X-Portal-Instance-ID": id },
  });
}

afterEach(() => {
  document.head.querySelectorAll('meta[name^="portal-"]').forEach((tag) => tag.remove());
  localStorage.removeItem(storageKey);
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("portal instance binding", () => {
  it("keeps standalone authentication when document metadata is absent", () => {
    expect(readPortalContext()).toBeNull();
    saveSecret("standalone-secret");
    expect(loadSavedSecret()).toBe("standalone-secret");
  });

  it("rejects an incomplete context or unsafe portal navigation URL", () => {
    portal();
    document.head.querySelector<HTMLMetaElement>('meta[name="portal-url"]')!.content = "javascript:alert(1)";
    expect(readPortalContext).toThrow("Invalid portal URL");
    document.head.querySelector('meta[name="portal-url"]')!.remove();
    expect(readPortalContext).toThrow("Incomplete portal metadata");
  });

  it("binds bootstrap and suppresses saved, supplied and URL-fragment passwords", async () => {
    portal();
    localStorage.setItem(storageKey, "saved-secret");
    window.history.replaceState(null, "", "/#/settings?bootstrapSecret=url-secret&section=models");
    expect(consumeUrlBootstrapSecret()).toBe("");
    expect(window.location.hash).toBe("#/settings?section=models");
    expect(loadSavedSecret()).toBe("");
    saveSecret("must-not-save");
    expect(localStorage.getItem(storageKey)).toBe("saved-secret");
    const fetcher = vi.fn(async () => response());
    vi.stubGlobal("fetch", fetcher);
    await expect(fetchBootstrap("", "supplied-secret")).resolves.toMatchObject({ token: "short-ws" });
    const init = vi.mocked(fetch).mock.calls[0][1];
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Portal-Expected-Instance")).toBe(instanceId);
    expect(headers.has("X-Nanobot-Auth")).toBe(false);
    expect(headers.has("Authorization")).toBe(false);
    expect(init?.credentials).toBe("same-origin");
    expect(init?.redirect).toBe("error");
  });

  it("binds terminal probes even with the no-timeout fetch path", async () => {
    portal();
    vi.stubGlobal("fetch", vi.fn(async () => response()));
    await fetchWithTimeout("/webui/terminal", { headers: { "X-Nanobot-Auth": "old", Authorization: "Bearer old" } }, 0);
    const headers = new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers);
    expect(headers.get("X-Portal-Expected-Instance")).toBe(instanceId);
    expect(headers.has("X-Nanobot-Auth")).toBe(false);
    expect(headers.has("Authorization")).toBe(false);
  });

  it.each(["nanobot:user-b", null])("rejects bootstrap from another or unconfirmed instance: %s", async (id) => {
    portal();
    vi.stubGlobal("fetch", vi.fn(async () => response(200, id)));
    await expect(fetchBootstrap()).rejects.toMatchObject({ name: "PortalAccessError", reason: "instance" });
  });

  it("rejects a stale tab's 409 even when the gateway echoes its expected ID", async () => {
    portal();
    vi.stubGlobal("fetch", vi.fn(async () => response(409)));
    await expect(fetchBootstrap()).rejects.toBeInstanceOf(PortalAccessError);
  });

  it.each([401, 403])("treats unauthenticated portal HTTP %s without an ID as sign-in expiry", async (status) => {
    portal();
    vi.stubGlobal("fetch", vi.fn(async () => response(status, null)));
    await expect(fetchBootstrap()).rejects.toMatchObject({ reason: "expired" });
  });

  it("rejects another instance's API response while retaining normal scope errors", async () => {
    portal();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(200, "nanobot:user-b")).mockResolvedValueOnce(response(403)));
    await expect(fetchWithTimeout("/api/sessions", { headers: { Authorization: "Bearer short-api" } })).rejects.toMatchObject({ reason: "instance" });
    expect((await fetchWithTimeout("/api/files")).status).toBe(403);
  });

  it("never sends portal credentials to another origin", async () => {
    portal();
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    await expect(fetchBootstrap("https://other.example", "supplied-secret")).rejects.toThrow("this app's origin");
    await expect(fetchWithTimeout("https://other.example/api/sessions", { headers: { Authorization: "Bearer short-api" } })).rejects.toThrow("this app's origin");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("binds same-origin WebSockets and rejects foreign hosts before using tokens", () => {
    portal();
    const url = new URL(deriveWsUrl("/ws", "short token"));
    expect(url.host).toBe(window.location.host);
    expect(url.protocol).toBe("ws:");
    expect(url.searchParams.get("portal_instance")).toBe(instanceId);
    expect(url.searchParams.get("token")).toBe("short token");
    expect(() => deriveWsUrl("/ws", "short token", "wss://other.example/ws")).toThrow("this app's origin");
  });
});
