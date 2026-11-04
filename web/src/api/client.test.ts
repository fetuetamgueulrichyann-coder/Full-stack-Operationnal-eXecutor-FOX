import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError, api, getCsrfToken, onUnauthorized, setCsrfToken } from "./client";
import { describeError } from "./errors";
import { mockApi } from "../test/mockApi";

beforeEach(() => {
  setCsrfToken("");
  onUnauthorized(null);
});
afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("envoie les cookies avec chaque requête et préfixe /api", async () => {
    const { calls } = mockApi({ "GET /tasks": () => ({ body: { tasks: [] } }) });
    await api("/tasks");
    expect(calls[0].credentials).toBe("include");
    expect(calls[0].path).toBe("/tasks");
  });

  it("n'envoie ni Content-Type ni jeton CSRF sur une lecture", async () => {
    const { calls } = mockApi({ "GET /tasks": () => ({ body: {} }) });
    setCsrfToken("abc");
    await api("/tasks");
    expect(calls[0].headers["X-CSRF-Token"]).toBeUndefined();
    expect(calls[0].headers["Content-Type"]).toBeUndefined();
  });

  it("garde le jeton CSRF de la connexion et l'envoie sur les requêtes modifiantes", async () => {
    const { calls } = mockApi({
      "POST /auth/login": () => ({ body: { user: {}, csrf_token: "jeton-123" } }),
      "POST /chats": () => ({ body: { chat_id: 4 } }),
      "DELETE /tasks/1": () => ({ body: { status: "deleted" } }),
    });
    await api("/auth/login", { method: "POST", body: { username: "a", password: "b" } });
    expect(getCsrfToken()).toBe("jeton-123");
    await api("/chats", { method: "POST" });
    await api("/tasks/1", { method: "DELETE" });
    expect(calls[1].headers["X-CSRF-Token"]).toBe("jeton-123");
    expect(calls[2].headers["X-CSRF-Token"]).toBe("jeton-123");
    expect(calls[1].headers["Content-Type"]).toBe("application/json");
    expect(calls[1].body).toEqual({});
  });

  it("transforme une erreur du serveur en ApiError avec statut et code", async () => {
    mockApi({ "POST /message": () => ({ status: 429, body: { error: "plafond atteint", code: "quota_exceeded", scope: "user" } }) });
    const error = await api("/message", { method: "POST" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(429);
    expect(apiError.code).toBe("quota_exceeded");
    expect(apiError.data.scope).toBe("user");
  });

  it("signale une panne réseau par NetworkError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/tasks")).rejects.toBeInstanceOf(NetworkError);
  });

  it("laisse passer l'annulation (AbortError)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("annulé", "AbortError")));
    await expect(api("/tasks")).rejects.toMatchObject({ name: "AbortError" });
  });

  it("prévient quand la session a expiré, mais pas pour un mauvais mot de passe", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    mockApi({
      "GET /tasks": () => ({ status: 401, body: { error: "non authentifie", code: "unauthenticated" } }),
      "POST /auth/login": () => ({ status: 401, body: { error: "incorrect", code: "invalid_credentials" } }),
    });
    await api("/tasks").catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    await api("/auth/login", { method: "POST" }).catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("supporte une réponse qui n'est pas du JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>502</html>", { status: 502 })));
    const error = (await api("/tasks").catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(502);
    expect(error.code).toBe("error");
  });
});

describe("describeError", () => {
  it("donne un message clair pour les codes connus", () => {
    expect(describeError(new ApiError(429, "quota_exceeded", "x"))).toContain("limite");
    expect(describeError(new ApiError(503, "model_unavailable", "x"))).toContain("indisponible");
    expect(describeError(new ApiError(403, "csrf_invalid", "x"))).toContain("Rechargez");
    expect(describeError(new ApiError(400, "bad_request", "Texte invalide"))).toBe("Texte invalide");
    expect(describeError(new NetworkError())).toContain("joindre");
    expect(describeError(new DOMException("a", "AbortError"))).toContain("trop de temps");
    expect(describeError(new Error("secret technique"))).not.toContain("secret");
  });
});
