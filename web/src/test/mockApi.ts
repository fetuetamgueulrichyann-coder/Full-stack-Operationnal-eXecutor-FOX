import { vi } from "vitest";

export interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
  credentials: RequestCredentials | undefined;
}

export type Handler = (call: Call) => { status?: number; body?: unknown } | Promise<{ status?: number; body?: unknown }>;

/** Remplace fetch : chaque route « METHODE /chemin » (sans le préfixe /api) a son gestionnaire. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace(/^\/api/, "");
    const method = (init?.method ?? "GET").toUpperCase();
    const call: Call = {
      method,
      path: url,
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      credentials: init?.credentials,
    };
    calls.push(call);
    const handler = routes[`${method} ${url}`];
    if (!handler) return new Response(JSON.stringify({ error: "introuvable", code: "not_found" }), { status: 404 });
    const result = await handler(call);
    return new Response(JSON.stringify(result.body ?? {}), {
      status: result.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn };
}

export const USER = { id: 1, username: "marie", role: "tester", timezone: null } as const;
export const unauthenticated = () => ({ status: 401, body: { error: "non authentifie", code: "unauthenticated" } });
