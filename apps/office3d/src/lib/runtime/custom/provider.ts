import type {
  EventFrame,
  GatewayConnectOptions,
  GatewayGapInfo,
  GatewayStatus,
} from "@/lib/gateway/GatewayClient";
import type { GatewayClient } from "@/lib/gateway/GatewayClient";
import {
  buildAgentMainSessionKey,
  parseAgentIdFromSessionKey,
} from "@/lib/gateway/GatewayClient";
import {
  fetchCustomRuntimeJson,
  normalizeCustomBaseUrl,
  requestCustomRuntime,
  requestCustomRuntimeStream,
} from "@/lib/runtime/custom/http";
import { normalizeGatewayEvent } from "@/lib/runtime/openclaw/normalizeGatewayEvent";
import type { RuntimeCapability, RuntimeEvent, RuntimeProvider } from "@/lib/runtime/types";

const CUSTOM_RUNTIME_CAPABILITIES: ReadonlySet<RuntimeCapability> = new Set([
  "agents",
  "sessions",
  "chat",
  "streaming",
  "runtime-agent-events",
  "models",
  "agent-roles",
]);

type CustomRuntimeStateResponse = {
  profileName?: string | null;
  registry_profile?: string | null;
  active?: Record<string, unknown> | null;
  profile?: string | null;
  identity?: {
    name?: string | null;
    role?: string | null;
    lane?: string | null;
    model_id?: string | null;
  } | null;
  runtime?: {
    name?: string | null;
    version?: string | null;
    vendor?: string | null;
    status?: string | null;
    active_model?: string | null;
    governance?: string | null;
  } | null;
  [key: string]: unknown;
};

type CustomRuntimeRegistryResponse = {
  models?: Record<string, unknown> | null;
  agents?: Array<{
    id?: string | null;
    name?: string | null;
    role?: string | null;
    identity?: {
      name?: string | null;
      avatar?: string | null;
      avatarUrl?: string | null;
    } | null;
  }> | null;
  sessionDefaults?: {
    mainKey?: string | null;
    scope?: string | null;
  } | null;
  [key: string]: unknown;
};

type CustomRuntimeHealthResponse = {
  ok?: boolean;
  status?: string;
  [key: string]: unknown;
};

type SyntheticAgent = {
  id: string;
  name: string;
  role: string | null;
};

type SessionMessage = {
  role: "user" | "assistant";
  text: string;
  timestamp: number;
};

type SessionRecord = {
  sessionKey: string;
  agentId: string;
  role: string | null;
  model: string | null;
  thinkingLevel: string | null;
  updatedAt: number | null;
  messages: SessionMessage[];
};

type ActiveRunRecord = {
  runId: string;
  sessionKey: string;
  controller: AbortController;
};

type SessionsListResult = {
  sessions?: Array<{
    key?: string | null;
    updatedAt?: number | null;
    displayName?: string | null;
    origin?: { label?: string | null; provider?: string | null } | null;
    thinkingLevel?: string | null;
    modelProvider?: string | null;
    model?: string | null;
    execHost?: string | null;
    execSecurity?: string | null;
    execAsk?: string | null;
  }> | null;
};

type StatusResult = {
  sessions?: {
    recent?: Array<{ key?: string | null; updatedAt?: number | null }> | null;
    byAgent?: Array<{
      agentId?: string | null;
      recent?: Array<{ key?: string | null; updatedAt?: number | null }> | null;
    }> | null;
  } | null;
  [key: string]: unknown;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));

const titleCase = (value: string): string =>
  value
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");

const resolveRouteProfile = (state: CustomRuntimeStateResponse | null): string | null => {
  if (!state) return null;
  if (typeof state.profileName === "string" && state.profileName.trim()) return state.profileName.trim();
  if (typeof state.registry_profile === "string" && state.registry_profile.trim()) {
    return state.registry_profile.trim();
  }
  if (typeof state.profile === "string" && state.profile.trim()) return state.profile.trim();
  return null;
};

const extractContentText = (content: unknown): string => {
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === "string") return item;
        if (isRecord(item) && typeof item.text === "string") return item.text;
        return "";
      })
      .join("")
      .trim();
  }
  return "";
};

const resolveAssistantTextFromResponse = (payload: unknown): string | null => {
  if (!isRecord(payload)) return null;
  const choices = Array.isArray(payload.choices) ? payload.choices : [];
  const first = choices[0];
  if (!isRecord(first)) return null;
  const message = isRecord(first.message) ? first.message : null;
  const direct = extractContentText(message?.content);
  if (direct) return direct;
  const text = extractContentText(first.text);
  return text || null;
};

const parseSseFrame = (block: string): EventFrame | null => {
  const dataLines = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.replace(/^data:\s?/, ""));
  const data = dataLines.join("\n").trim();
  if (!data || data === "[DONE]") return null;
  try {
    const parsed = JSON.parse(data) as unknown;
    if (!isRecord(parsed) || parsed.type !== "event" || typeof parsed.event !== "string") {
      return null;
    }
    return parsed as EventFrame;
  } catch {
    return null;
  }
};

const resolveChatState = (frame: EventFrame): string | null => {
  const payload = isRecord(frame.payload) ? frame.payload : null;
  return typeof payload?.state === "string" ? payload.state : null;
};

const resolveAssistantTextFromEvent = (frame: EventFrame): string | null => {
  if (frame.event !== "chat") return null;
  const payload = isRecord(frame.payload) ? frame.payload : null;
  if (!payload) return null;
  const direct = extractContentText(payload.text);
  if (direct) return direct;
  return extractContentText(isRecord(payload.message) ? payload.message.content : null) || null;
};

const normalizeModelChoices = (registry: CustomRuntimeRegistryResponse | null): string[] => {
  if (!registry || !isRecord(registry.models)) return [];
  return Object.keys(registry.models).map((value) => value.trim()).filter(Boolean);
};

const resolveOptionalString = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

const resolveDefaultModelId = (
  state: CustomRuntimeStateResponse | null,
  modelChoices: string[]
): string | null => {
  return (
    resolveOptionalString(state?.identity?.model_id) ??
    resolveOptionalString(state?.runtime?.active_model) ??
    modelChoices[0] ??
    null
  );
};

const buildIdentityAgent = (
  state: CustomRuntimeStateResponse | null,
  runtimeName: string
): SyntheticAgent | null => {
  const name = resolveOptionalString(state?.identity?.name);
  const role = resolveOptionalString(state?.identity?.role) ?? "assistant";
  const lane = resolveOptionalString(state?.identity?.lane);
  if (!name && !lane && !role) return null;
  return {
    id: lane ?? role ?? "main",
    name: name ?? titleCase(lane ?? runtimeName),
    role,
  };
};

const buildChatFailureMessage = (
  statusCode: number,
  responseText: string,
  health: CustomRuntimeHealthResponse | null
): string => {
  const trimmed = responseText.trim();
  if (trimmed) return trimmed;
  const healthStatus = resolveOptionalString(health?.status);
  if (healthStatus) {
    return `Custom runtime chat failed (${statusCode}). Runtime health is ${healthStatus}.`;
  }
  return `Custom runtime chat failed (${statusCode}).`;
};

const buildSyntheticAgents = (
  state: CustomRuntimeStateResponse | null,
  runtimeName: string
): SyntheticAgent[] => {
  const active = isRecord(state?.active) ? state.active : null;
  if (active) {
    const agents: SyntheticAgent[] = [];
    for (const [roleKey, value] of Object.entries(active)) {
      const role = roleKey.trim();
      if (!role) continue;
      const hasModels =
        (typeof value === "string" && value.trim()) ||
        (Array.isArray(value) && value.some((entry) => typeof entry === "string" && entry.trim()));
      if (!hasModels) continue;
      agents.push({
        id: role,
        name: titleCase(role),
        role,
      });
    }
    if (agents.length > 0) {
      return agents;
    }
  }
  const identityAgent = buildIdentityAgent(state, runtimeName);
  if (identityAgent) {
    return [identityAgent];
  }
  return [
    {
      id: "main",
      name: runtimeName,
      role: "assistant",
    },
  ];
};

const buildRegistryAgents = (registry: CustomRuntimeRegistryResponse | null): SyntheticAgent[] => {
  const agents = Array.isArray(registry?.agents) ? registry.agents : [];
  return agents.flatMap((agent) => {
    const id = resolveOptionalString(agent?.id);
    if (!id) return [];
    const name =
      resolveOptionalString(agent?.name) ??
      resolveOptionalString(agent?.identity?.name) ??
      titleCase(id);
    return [
      {
        id,
        name,
        role: resolveOptionalString(agent?.role),
      },
    ];
  });
};

const buildQuery = (params: Record<string, string | number | null | undefined>): string => {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
};

export class CustomRuntimeProvider implements RuntimeProvider {
  readonly id = "custom" as const;
  readonly label = "Custom";
  readonly capabilities = CUSTOM_RUNTIME_CAPABILITIES;
  readonly metadata;
  private readonly baseUrl: string;
  private readonly sessions = new Map<string, SessionRecord>();
  private readonly activeRunsByRunId = new Map<string, ActiveRunRecord>();
  private readonly activeRunIdBySessionKey = new Map<string, string>();
  private readonly eventHandlers = new Set<(event: EventFrame) => void>();

  constructor(
    readonly client: GatewayClient,
    runtimeUrl: string
  ) {
    this.baseUrl = normalizeCustomBaseUrl(runtimeUrl);
    this.metadata = {
      id: this.id,
      label: this.label,
      runtimeName: "Custom Runtime",
      routeProfile: null,
    };
  }

  connect(options: GatewayConnectOptions): Promise<void> {
    return this.client.connect(options);
  }

  disconnect(): void {
    this.client.disconnect();
  }

  async call<T = unknown>(method: string, params: unknown): Promise<T> {
    switch (method) {
      case "agents.list":
        return (await this.callAgentsList()) as T;
      case "sessions.list":
        return (await this.callSessionsList(params)) as T;
      case "status":
        return (await this.callStatus()) as T;
      case "models.list":
        return (await this.callModelsList()) as T;
      case "sessions.preview":
        return (await this.callSessionsPreview(params)) as T;
      case "sessions.patch":
        return (await this.callSessionsPatch(params)) as T;
      case "chat.history":
        return (await this.callChatHistory(params)) as T;
      case "chat.send":
        return (await this.callChatSend(params)) as T;
      case "chat.abort":
        return (await this.callChatAbort(params)) as T;
      case "sessions.reset":
        return (await this.callSessionsReset(params)) as T;
      case "agent.wait":
        return (await this.callAgentWait(params)) as T;
      case "exec.approvals.get":
        return ({ file: { agents: {} } } as T);
      case "config.get":
      case "config.patch":
      case "config.set":
        throw new Error(`Custom runtime does not support ${method}.`);
      default:
        throw new Error(`Custom runtime does not implement ${method}.`);
    }
  }

  onStatus(handler: (status: GatewayStatus) => void): () => void {
    return this.client.onStatus(handler);
  }

  onGap(handler: (info: GatewayGapInfo) => void): () => void {
    return this.client.onGap(handler);
  }

  onEvent(handler: (event: EventFrame) => void): () => void {
    this.eventHandlers.add(handler);
    const unsubscribeGateway = this.client.onEvent(handler);
    return () => {
      this.eventHandlers.delete(handler);
      unsubscribeGateway();
    };
  }

  onRuntimeEvent(handler: (event: RuntimeEvent) => void): () => void {
    return this.onEvent((event) => {
      handler(normalizeGatewayEvent(event));
    });
  }

  async fetchHealth(): Promise<CustomRuntimeHealthResponse> {
    return this.fetchJson<CustomRuntimeHealthResponse>("/health");
  }

  async fetchState(): Promise<CustomRuntimeStateResponse> {
    return this.fetchJson<CustomRuntimeStateResponse>("/state");
  }

  async fetchRegistry(): Promise<CustomRuntimeRegistryResponse> {
    return this.fetchJson<CustomRuntimeRegistryResponse>("/registry");
  }

  async describeRuntime() {
    const [health, state, registry] = await Promise.all([
      this.fetchHealth().catch(() => null),
      this.fetchState().catch(() => null),
      this.fetchRegistry().catch(() => null),
    ]);

    const routeProfile = resolveRouteProfile(state);
    const runtimeName =
      typeof state?.runtime?.name === "string" && state.runtime.name.trim()
        ? state.runtime.name.trim()
        : this.metadata.runtimeName;
    const runtimeVersion =
      typeof state?.runtime?.version === "string" && state.runtime.version.trim()
        ? state.runtime.version.trim()
        : null;
    const vendor =
      typeof state?.runtime?.vendor === "string" && state.runtime.vendor.trim()
        ? state.runtime.vendor.trim()
        : null;

    return {
      metadata: {
        ...this.metadata,
        runtimeName,
        runtimeVersion,
        vendor,
        routeProfile,
      },
      health,
      state,
      registry,
    };
  }

  private async callAgentsList() {
    const descriptor = await this.describeRuntime();
    const runtimeName = descriptor.metadata.runtimeName ?? this.metadata.runtimeName ?? "Custom Runtime";
    const registryAgents = buildRegistryAgents(descriptor.registry);
    const agents = registryAgents.length > 0 ? registryAgents : buildSyntheticAgents(descriptor.state, runtimeName);
    const mainKey = resolveOptionalString(descriptor.registry?.sessionDefaults?.mainKey) ?? "main";
    return {
      defaultId: agents[0]?.id ?? "main",
      mainKey,
      scope: resolveOptionalString(descriptor.registry?.sessionDefaults?.scope) ?? "custom",
      agents: agents.map((agent) => ({
        id: agent.id,
        name: agent.name,
        role: agent.role,
      })),
    };
  }

  private async callSessionsList(rawParams: unknown) {
    const params = isRecord(rawParams) ? rawParams : {};
    const agentId = typeof params.agentId === "string" ? params.agentId.trim() : "";
    const search = typeof params.search === "string" ? params.search.trim() : "";
    const limit = typeof params.limit === "number" && Number.isFinite(params.limit) ? params.limit : 50;
    const descriptor = await this.describeRuntime();
    const pathname = `/sessions${buildQuery({
      agent_id: agentId || undefined,
      search: search || undefined,
      limit,
      main_key: resolveOptionalString(descriptor.registry?.sessionDefaults?.mainKey) ?? "main",
    })}`;
    try {
      const payload = await this.fetchJson<SessionsListResult>(pathname);
      const sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
      return {
        sessions: sessions.map((session) => ({
          key: session.key,
          updatedAt: session.updatedAt ?? null,
          displayName: session.displayName ?? agentId,
          origin: session.origin ?? {
            label: descriptor.metadata.runtimeName ?? "Custom Runtime",
            provider: "custom",
          },
          modelProvider: session.modelProvider ?? "custom",
          model: session.model ?? resolveDefaultModelId(descriptor.state, normalizeModelChoices(descriptor.registry)),
          thinkingLevel: session.thinkingLevel ?? undefined,
          execHost: session.execHost ?? undefined,
          execSecurity: session.execSecurity ?? undefined,
          execAsk: session.execAsk ?? undefined,
        })),
      };
    } catch {
      const modelChoices = normalizeModelChoices(descriptor.registry);
      const sessions = agentId
        ? [this.ensureSession(buildAgentMainSessionKey(agentId, "main"), agentId, resolveDefaultModelId(descriptor.state, modelChoices))]
        : [...this.sessions.values()];
      return {
        sessions: sessions.map((session) => ({
          key: session.sessionKey,
          updatedAt: session.updatedAt,
          displayName: session.agentId,
          origin: {
            label: descriptor.metadata.runtimeName ?? "Custom Runtime",
            provider: "custom",
          },
          modelProvider: "custom",
          model: session.model,
        })),
      };
    }
  }

  private async callStatus() {
    try {
      return await this.fetchJson<StatusResult>("/status");
    } catch {
      return {
        sessions: {
          recent: [...this.sessions.values()].map((session) => ({
            key: session.sessionKey,
            updatedAt: session.updatedAt,
          })),
          byAgent: [...this.sessions.values()].map((session) => ({
            agentId: session.agentId,
            recent: [
              {
                key: session.sessionKey,
                updatedAt: session.updatedAt,
              },
            ],
          })),
        },
      };
    }
  }

  private async callModelsList() {
    const descriptor = await this.describeRuntime();
    const modelIds = normalizeModelChoices(descriptor.registry);
    return {
      models: modelIds.map((id) => ({
        id,
        name: id,
        provider: "custom",
      })),
    };
  }

  private async callSessionsPreview(rawParams: unknown) {
    const params = isRecord(rawParams) ? rawParams : {};
    const keys = Array.isArray(params.keys)
      ? params.keys.filter((value): value is string => typeof value === "string")
      : [];
    try {
      return await requestCustomRuntime({
        runtimeUrl: this.baseUrl,
        pathname: "/sessions/preview",
        method: "POST",
        body: {
          keys,
          limit: typeof params.limit === "number" ? params.limit : 8,
          maxChars: typeof params.maxChars === "number" ? params.maxChars : 240,
        },
      });
    } catch {
      // Fall through to local previews below for older runtimes.
    }
    return {
      ts: Date.now(),
      previews: keys.map((key) => {
        const session = this.sessions.get(key) ?? null;
        const items = session
          ? session.messages.slice(-8).map((message) => ({
              role: message.role,
              text: message.text,
              timestamp: message.timestamp,
            }))
          : [];
        return {
          key,
          status: items.length > 0 ? "ok" : "empty",
          items,
        };
      }),
    };
  }

  private async callChatHistory(rawParams: unknown) {
    const params = isRecord(rawParams) ? rawParams : {};
    const sessionKey = typeof params.sessionKey === "string" ? params.sessionKey.trim() : "";
    if (!sessionKey) {
      throw new Error("Custom runtime requires sessionKey for chat.history.");
    }
    try {
      return await this.fetchJson(
        `/sessions/history${buildQuery({
          session_id: sessionKey,
          limit: typeof params.limit === "number" ? params.limit : 200,
        })}`
      );
    } catch {
      // Fall through to local in-memory history for older runtimes.
    }
    const session = this.sessions.get(sessionKey) ?? null;
    return {
      sessionKey,
      messages: (session?.messages ?? []).map((message) => ({
        role: message.role,
        content: message.text,
        timestamp: message.timestamp,
      })),
    };
  }

  private async callSessionsPatch(rawParams: unknown) {
    const params = isRecord(rawParams) ? rawParams : {};
    const key = typeof params.key === "string" ? params.key.trim() : "";
    if (!key) {
      throw new Error("Custom runtime requires key for sessions.patch.");
    }
    const descriptor = await this.describeRuntime();
    const modelChoices = normalizeModelChoices(descriptor.registry);
    const agentId = parseAgentIdFromSessionKey(key) ?? "main";
    const session = this.ensureSession(
      key,
      agentId,
      resolveDefaultModelId(descriptor.state, modelChoices)
    );
    if ("model" in params) {
      session.model = resolveOptionalString(params.model) ?? resolveDefaultModelId(descriptor.state, modelChoices);
    }
    if ("thinkingLevel" in params) {
      session.thinkingLevel = resolveOptionalString(params.thinkingLevel);
    }
    session.updatedAt = Date.now();
    return {
      ok: true,
      key,
      entry: {
        ...(session.thinkingLevel ? { thinkingLevel: session.thinkingLevel } : null),
      },
      resolved: {
        modelProvider: "custom",
        ...(session.model ? { model: session.model } : null),
      },
    };
  }

  private async callChatSend(rawParams: unknown) {
    const params = isRecord(rawParams) ? rawParams : {};
    const sessionKey = typeof params.sessionKey === "string" ? params.sessionKey.trim() : "";
    const message = typeof params.message === "string" ? params.message.trim() : "";
    const runId = typeof params.idempotencyKey === "string" ? params.idempotencyKey.trim() : "";
    if (!sessionKey || !message) {
      throw new Error("Custom runtime requires sessionKey and message for chat.send.");
    }
    const agentId = parseAgentIdFromSessionKey(sessionKey) ?? "main";
    const descriptor = await this.describeRuntime();
    const modelChoices = normalizeModelChoices(descriptor.registry);
    const session = this.ensureSession(
      sessionKey,
      agentId,
      resolveDefaultModelId(descriptor.state, modelChoices)
    );
    const resolvedRole = session.role ?? agentId;
    const resolvedLane = agentId;
    const resolvedRunId = runId || `custom-${Date.now().toString(36)}`;
    const controller = new AbortController();
    if (resolvedRunId) {
      const activeRun: ActiveRunRecord = { runId: resolvedRunId, sessionKey, controller };
      this.activeRunsByRunId.set(resolvedRunId, activeRun);
      this.activeRunIdBySessionKey.set(sessionKey, resolvedRunId);
    }
    const userTimestamp = Date.now();
    session.messages.push({
      role: "user",
      text: message,
      timestamp: userTimestamp,
    });
    session.updatedAt = userTimestamp;

    void this.runChatStream({
      controller,
      session,
      sessionKey,
      runId: resolvedRunId,
      agentId,
      role: resolvedRole,
      lane: resolvedLane,
    });
    return {
      status: "started",
      runId: resolvedRunId,
    };
  }

  private async callChatAbort(rawParams: unknown) {
    const params = isRecord(rawParams) ? rawParams : {};
    const runId = typeof params.runId === "string" ? params.runId.trim() : "";
    const sessionKey = typeof params.sessionKey === "string" ? params.sessionKey.trim() : "";
    const targetRunId = runId || (sessionKey ? this.activeRunIdBySessionKey.get(sessionKey) ?? "" : "");
    if (!targetRunId) {
      return { ok: true };
    }
    const activeRun = this.activeRunsByRunId.get(targetRunId) ?? null;
    activeRun?.controller.abort();
    this.activeRunsByRunId.delete(targetRunId);
    if (activeRun?.sessionKey) {
      const activeSessionRunId = this.activeRunIdBySessionKey.get(activeRun.sessionKey);
      if (activeSessionRunId === targetRunId) {
        this.activeRunIdBySessionKey.delete(activeRun.sessionKey);
      }
    }
    return { ok: true };
  }

  private async runChatStream(params: {
    controller: AbortController;
    session: SessionRecord;
    sessionKey: string;
    runId: string;
    agentId: string;
    role: string;
    lane: string;
  }): Promise<void> {
    const { controller, session, sessionKey, runId, agentId, role, lane } = params;
    try {
      const response = await requestCustomRuntimeStream({
        runtimeUrl: this.baseUrl,
        pathname: "/v1/chat/completions",
        method: "POST",
        signal: controller.signal,
        body: {
          model: session.model ?? undefined,
          stream: true,
          idempotencyKey: runId,
          run_id: runId,
          agent_id: agentId,
          role,
          lane,
          conversation_id: sessionKey,
          session_id: sessionKey,
          messages: session.messages.map((entry) => ({
            role: entry.role,
            content: entry.text,
          })),
        },
      });
      const contentType = response.headers.get("content-type") ?? "";
      if (contentType.includes("text/event-stream") && response.body) {
        await this.readSseFrames(response, (frame) => {
          this.emitEvent(frame);
          const assistantText = resolveAssistantTextFromEvent(frame);
          if (assistantText && frame.event === "chat") {
            session.updatedAt = Date.now();
            if (resolveChatState(frame) === "final") {
              session.messages.push({
                role: "assistant",
                text: assistantText,
                timestamp: session.updatedAt,
              });
            }
          }
        });
        return;
      }
      const payload = (await response.json().catch(() => null)) as unknown;
      const assistantText = resolveAssistantTextFromResponse(payload);
      if (!assistantText) {
        throw new Error("Custom runtime returned an empty assistant response.");
      }
      const assistantTimestamp = Date.now();
      session.messages.push({
        role: "assistant",
        text: assistantText,
        timestamp: assistantTimestamp,
      });
      session.updatedAt = assistantTimestamp;
      this.emitChatLifecycle(runId, sessionKey, "start");
      this.emitChatFinal(runId, sessionKey, assistantText);
      this.emitChatLifecycle(runId, sessionKey, "end");
    } catch (error) {
      if (controller.signal.aborted) {
        this.emitChatEvent(runId, sessionKey, "aborted", "Run aborted.");
        return;
      }
      const health = await this.fetchHealth().catch(() => null);
      const message = buildChatFailureMessage(
        502,
        error instanceof Error ? error.message : String(error),
        health
      );
      this.emitChatLifecycle(runId, sessionKey, "error");
      this.emitChatEvent(runId, sessionKey, "error", message);
    } finally {
      this.activeRunsByRunId.delete(runId);
      const activeSessionRunId = this.activeRunIdBySessionKey.get(sessionKey);
      if (activeSessionRunId === runId) {
        this.activeRunIdBySessionKey.delete(sessionKey);
      }
    }
  }

  private async readSseFrames(
    response: Response,
    onFrame: (frame: EventFrame) => void
  ): Promise<void> {
    const reader = response.body?.getReader();
    if (!reader) return;
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const frame = parseSseFrame(part);
        if (frame) onFrame(frame);
      }
    }
    const remaining = decoder.decode();
    if (remaining) buffer += remaining;
    const frame = parseSseFrame(buffer);
    if (frame) onFrame(frame);
  }

  private emitChatLifecycle(
    runId: string,
    sessionKey: string,
    phase: "start" | "end" | "error"
  ): void {
    this.emitEvent({
      type: "event",
      event: "agent",
      payload: {
        runId,
        sessionKey,
        stream: "lifecycle",
        data: { phase },
      },
    });
  }

  private emitChatFinal(runId: string, sessionKey: string, text: string): void {
    this.emitChatEvent(runId, sessionKey, "final", text);
  }

  private emitChatEvent(
    runId: string,
    sessionKey: string,
    state: "final" | "error" | "aborted",
    text: string
  ): void {
    this.emitEvent({
      type: "event",
      event: "chat",
      payload: {
        runId,
        sessionKey,
        state,
        text,
        message: {
          role: "assistant",
          content: text,
          timestamp: Date.now(),
        },
        ...(state === "error" ? { errorMessage: text } : null),
      },
    });
  }

  private emitEvent(frame: EventFrame): void {
    for (const handler of this.eventHandlers) {
      handler(frame);
    }
  }

  private async callSessionsReset(rawParams: unknown) {
    const params = isRecord(rawParams) ? rawParams : {};
    const key = typeof params.key === "string" ? params.key.trim() : "";
    if (!key) {
      throw new Error("Custom runtime requires key for sessions.reset.");
    }
    this.sessions.delete(key);
    const activeRunId = this.activeRunIdBySessionKey.get(key);
    if (activeRunId) {
      this.activeRunsByRunId.get(activeRunId)?.controller.abort();
      this.activeRunsByRunId.delete(activeRunId);
      this.activeRunIdBySessionKey.delete(key);
    }
    try {
      await requestCustomRuntime({
        runtimeUrl: this.baseUrl,
        pathname: "/sessions/reset",
        method: "POST",
        body: { key, session_id: key },
      });
    } catch {
      // Older custom runtimes may not expose server-side reset yet.
    }
    return { ok: true };
  }

  private async callAgentWait(rawParams: unknown) {
    const params = isRecord(rawParams) ? rawParams : {};
    const runId = typeof params.runId === "string" ? params.runId.trim() : "";
    return {
      status: runId && this.activeRunsByRunId.has(runId) ? "running" : "done",
    };
  }

  private ensureSession(sessionKey: string, agentId: string, model: string | null): SessionRecord {
    const existing = this.sessions.get(sessionKey);
    if (existing) return existing;
    const session: SessionRecord = {
      sessionKey,
      agentId,
      role: agentId || null,
      model,
      thinkingLevel: null,
      updatedAt: null,
      messages: [],
    };
    this.sessions.set(sessionKey, session);
    return session;
  }

  private async fetchJson<T = unknown>(pathname: string): Promise<T> {
    return fetchCustomRuntimeJson<T>(this.baseUrl, pathname);
  }
}
