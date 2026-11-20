// Client de l'API de FOX. Règles (voir docs/API.md) :
// - les cookies partent avec chaque requête (credentials: "include") ;
// - chaque requête qui modifie quelque chose envoie le jeton CSRF reçu à la connexion ;
// - toute erreur du serveur devient une ApiError avec le statut et le code stable.

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public data: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class NetworkError extends Error {
  constructor(message = "Impossible de joindre FOX. Vérifiez votre connexion.") {
    super(message);
    this.name = "NetworkError";
  }
}

let csrfToken = "";
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export function getCsrfToken(): string {
  return csrfToken;
}

/** Appelé quand le serveur répond « non authentifié » : la session a expiré. */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

async function request<T>(path: string, init: { method: string; headers: Record<string, string>; body?: BodyInit; signal?: AbortSignal }): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, credentials: "include" });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new NetworkError();
  }

  let data: Record<string, unknown> = {};
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    data = {};
  }

  if (!response.ok) {
    const code = typeof data.code === "string" ? data.code : "error";
    const message = typeof data.error === "string" ? data.error : "Erreur inattendue.";
    if (response.status === 401 && code === "unauthenticated") unauthorizedHandler?.();
    throw new ApiError(response.status, code, message, data);
  }

  if (typeof data.csrf_token === "string") csrfToken = data.csrf_token;
  return data as T;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const modifies = method !== "GET";
  const headers: Record<string, string> = {};
  if (modifies) {
    headers["Content-Type"] = "application/json";
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }
  return request<T>(path, {
    method,
    headers,
    signal: options.signal,
    body: modifies ? JSON.stringify(options.body ?? {}) : undefined,
  });
}

/** Envoi d'un fichier (formulaire multipart). Le navigateur fixe lui-même le Content-Type et sa frontière. */
export async function apiUpload<T>(path: string, form: FormData, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {};
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  return request<T>(path, { method: "POST", headers, body: form, signal });
}
