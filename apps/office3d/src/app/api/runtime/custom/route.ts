import { NextResponse } from "next/server";

export const runtime = "nodejs";

type CustomRuntimeRequestBody = {
  runtimeUrl?: string;
  pathname?: string;
  method?: string;
  body?: unknown;
};

const normalizeRuntimeUrl = (value: string): string => {
  const trimmed = value.trim();
  if (!trimmed) {
    throw new Error("runtimeUrl is required.");
  }
  const parsed = new URL(trimmed);
  if (parsed.protocol === "ws:") {
    parsed.protocol = "http:";
  } else if (parsed.protocol === "wss:") {
    parsed.protocol = "https:";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("runtimeUrl must use http, https, ws, or wss.");
  }
  parsed.username = "";
  parsed.password = "";
  return parsed.toString().replace(/\/$/, "");
};

const normalizePathname = (value: unknown): string => {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("pathname is required.");
  }
  const trimmed = value.trim();
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
};

const normalizeMethod = (value: unknown): "GET" | "POST" => {
  if (typeof value !== "string") return "GET";
  const upper = value.trim().toUpperCase();
  if (upper === "POST") return "POST";
  return "GET";
};

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as CustomRuntimeRequestBody;
    const runtimeUrl = normalizeRuntimeUrl(payload.runtimeUrl ?? "");
    const pathname = normalizePathname(payload.pathname);
    const method = normalizeMethod(payload.method);
    const response = await fetch(`${runtimeUrl}${pathname}`, {
      method,
      headers: {
        Accept: request.headers.get("accept") ?? "application/json",
        ...(method === "POST" ? { "Content-Type": "application/json" } : null),
      },
      body: method === "POST" ? JSON.stringify(payload.body ?? {}) : undefined,
      cache: "no-store",
    });
    const contentType = response.headers.get("content-type") ?? "application/json";
    if (contentType.includes("text/event-stream") && response.body) {
      return new NextResponse(response.body, {
        status: response.status,
        headers: {
          "Content-Type": contentType,
          "Cache-Control": "no-store",
          "X-Accel-Buffering": "no",
        },
      });
    }
    const text = await response.text();
    return new NextResponse(text, {
      status: response.status,
      headers: {
        "Content-Type": contentType,
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Custom runtime proxy failed.";
    return NextResponse.json({ error: message }, { status: 400 });
  }
}
