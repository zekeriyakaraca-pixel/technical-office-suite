// @vitest-environment node

import { afterEach, describe, expect, it, vi } from "vitest";
import type { EventFrame, GatewayClient } from "@/lib/gateway/GatewayClient";
import { buildAgentMainSessionKey } from "@/lib/gateway/GatewayClient";
import { CustomRuntimeProvider } from "@/lib/runtime/custom/provider";

const activeAgents = {
  "teknik-ofis-muduru": "technical-office/local",
  "autocad-uzman-1": "technical-office/local",
  "autocad-uzman-2": "technical-office/local",
  "kalite-kontrol": "technical-office/local",
  "dokuman-kontrol": "technical-office/local",
};

const createClient = (): GatewayClient =>
  ({
    connect: vi.fn(),
    disconnect: vi.fn(),
    onStatus: vi.fn(() => () => {}),
    onGap: vi.fn(() => () => {}),
    onEvent: vi.fn(() => () => {}),
  }) as unknown as GatewayClient;

type RouteValue =
  | unknown
  | Response
  | ((proxyBody: { pathname?: string; body?: unknown }) => Response | Promise<Response>);

const createProxyFetch = (routes: Record<string, RouteValue>) =>
  vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
    const proxyBody = JSON.parse(String(init?.body ?? "{}")) as { pathname?: string; body?: unknown };
    const pathname = proxyBody.pathname ?? "";
    if (!(pathname in routes)) {
      return new Response(JSON.stringify({ error: `Missing route: ${pathname}` }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    }
    const route = routes[pathname];
    if (route instanceof Response) return route;
    if (typeof route === "function") {
      return await route(proxyBody);
    }
    return new Response(JSON.stringify(route), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

const sseResponse = (frames: EventFrame[]) =>
  new Response(frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join(""), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });

describe("CustomRuntimeProvider", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("hydrates technical office agents from state.active", async () => {
    vi.stubGlobal(
      "fetch",
      createProxyFetch({
        "/health": { ok: true, status: "ready" },
        "/state": {
          profileName: "technical-office",
          active: activeAgents,
          runtime: { name: "Technical Office Runtime" },
        },
        "/registry": {
          models: { "technical-office/local": { name: "Technical Office Local Runtime" } },
        },
      })
    );

    const provider = new CustomRuntimeProvider(createClient(), "http://127.0.0.1:7770");
    const result = await provider.call<{
      agents: Array<{ id: string; role: string | null }>;
    }>("agents.list", {});

    expect(result.agents.map((agent) => agent.id)).toEqual(Object.keys(activeAgents));
    expect(result.agents[0]).toMatchObject({
      id: "teknik-ofis-muduru",
      role: "teknik-ofis-muduru",
    });
  });

  it("streams chat events from the custom completion response", async () => {
    vi.stubGlobal(
      "fetch",
      createProxyFetch({
        "/health": { ok: true, status: "ready" },
        "/state": {
          profileName: "technical-office",
          active: activeAgents,
          identity: {
            role: "teknik-ofis-muduru",
            lane: "teknik-ofis-muduru",
            model_id: "technical-office/local",
          },
          runtime: { name: "Technical Office Runtime", active_model: "technical-office/local" },
        },
        "/registry": {
          models: { "technical-office/local": { name: "Technical Office Local Runtime" } },
        },
        "/v1/chat/completions": sseResponse([
          {
            type: "event",
            event: "agent",
            payload: {
              runId: "job-test-001",
              sessionKey: buildAgentMainSessionKey("teknik-ofis-muduru", "main"),
              stream: "lifecycle",
              data: { phase: "start" },
            },
          },
          {
            type: "event",
            event: "chat",
            payload: {
              runId: "job-test-001",
              sessionKey: buildAgentMainSessionKey("teknik-ofis-muduru", "main"),
              state: "final",
              message: {
                role: "assistant",
                content:
                  "Job test-001 finished. ok=true\nDXF=workspace/outputs/jobs/test-001/1001/1001.dxf",
              },
            },
          },
        ]),
      })
    );

    const provider = new CustomRuntimeProvider(createClient(), "http://127.0.0.1:7770/");
    const events: EventFrame[] = [];
    provider.onEvent((event) => events.push(event));

    const result = await provider.call<{ status: string; runId: string }>("chat.send", {
      sessionKey: buildAgentMainSessionKey("teknik-ofis-muduru", "main"),
      message: "run job test-001 autocad off",
      idempotencyKey: "job-test-001",
    });

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(result.status).toBe("started");
    expect(result.runId).toBe("job-test-001");
    expect(events.map((event) => event.event)).toEqual(["agent", "chat"]);
    expect(JSON.stringify(events[1]?.payload)).toContain("workspace/outputs/jobs/test-001");
  });

  it("loads sessions from the server-side custom runtime", async () => {
    vi.stubGlobal(
      "fetch",
      createProxyFetch({
        "/health": { ok: true, status: "ready" },
        "/state": {
          profileName: "technical-office",
          active: activeAgents,
          runtime: { name: "Technical Office Runtime", active_model: "technical-office/local" },
        },
        "/registry": {
          models: { "technical-office/local": { name: "Technical Office Local Runtime" } },
          sessionDefaults: { mainKey: "main", scope: "custom" },
        },
        [`/sessions?agent_id=teknik-ofis-muduru&search=${encodeURIComponent(buildAgentMainSessionKey("teknik-ofis-muduru", "main"))}&limit=4&main_key=main`]: {
          sessions: [
            {
              key: buildAgentMainSessionKey("teknik-ofis-muduru", "main"),
              updatedAt: 1780000000000,
              displayName: "Teknik Ofis Muduru",
              origin: { label: "Technical Office Runtime", provider: "custom" },
              modelProvider: "custom",
              model: "technical-office/local",
            },
          ],
        },
      })
    );

    const provider = new CustomRuntimeProvider(createClient(), "http://127.0.0.1:7770/");
    const result = await provider.call<{
      sessions: Array<{ key: string; updatedAt: number | null; model: string | null }>;
    }>("sessions.list", {
      agentId: "teknik-ofis-muduru",
      search: buildAgentMainSessionKey("teknik-ofis-muduru", "main"),
      limit: 4,
    });

    expect(result.sessions).toEqual([
      expect.objectContaining({
        key: buildAgentMainSessionKey("teknik-ofis-muduru", "main"),
        updatedAt: 1780000000000,
        model: "technical-office/local",
      }),
    ]);
  });

  it("accepts session patches used by the chat composer", async () => {
    vi.stubGlobal(
      "fetch",
      createProxyFetch({
        "/health": { ok: true, status: "ready" },
        "/state": {
          profileName: "technical-office",
          active: activeAgents,
          runtime: { name: "Technical Office Runtime", active_model: "technical-office/local" },
        },
        "/registry": {
          models: { "technical-office/local": { name: "Technical Office Local Runtime" } },
        },
      })
    );

    const provider = new CustomRuntimeProvider(createClient(), "http://127.0.0.1:7770");
    const sessionKey = buildAgentMainSessionKey("teknik-ofis-muduru", "main");
    const result = await provider.call<{
      ok: true;
      key: string;
      entry?: { thinkingLevel?: string };
      resolved?: { modelProvider?: string; model?: string };
    }>("sessions.patch", {
      key: sessionKey,
      model: "technical-office/local",
      thinkingLevel: "xhigh",
    });

    expect(result).toMatchObject({
      ok: true,
      key: sessionKey,
      entry: { thinkingLevel: "xhigh" },
      resolved: { modelProvider: "custom", model: "technical-office/local" },
    });
  });
});
