import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, NetworkError, onUnauthorized, setCsrfToken } from "../api/client";
import { describeError } from "../api/errors";
import type { AuthResponse, User } from "../api/types";

export type AuthState =
  | { status: "loading" }
  | { status: "unreachable"; message: string }
  | { status: "anonymous" }
  | { status: "authenticated"; user: User };

interface AuthContextValue {
  state: AuthState;
  login: (username: string, password: string) => Promise<void>;
  register: (code: string, username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  // Au démarrage : « qui suis-je ? » (200 = connecté, 401 = page de connexion).
  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    api<AuthResponse>("/auth/me")
      .then((data) => {
        if (!cancelled) setState({ status: "authenticated", user: data.user });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof NetworkError) setState({ status: "unreachable", message: describeError(error) });
        else setState({ status: "anonymous" });
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // Session expirée pendant l'utilisation : retour à la connexion.
  useEffect(() => {
    onUnauthorized(() => {
      setCsrfToken("");
      setState({ status: "anonymous" });
    });
    return () => onUnauthorized(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const data = await api<AuthResponse>("/auth/login", { method: "POST", body: { username, password } });
    setState({ status: "authenticated", user: data.user });
  }, []);

  const register = useCallback(async (code: string, username: string, password: string) => {
    const data = await api<AuthResponse>("/auth/register", { method: "POST", body: { code, username, password } });
    setState({ status: "authenticated", user: data.user });
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setCsrfToken("");
      setState({ status: "anonymous" });
    }
  }, []);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const value = useMemo(() => ({ state, login, register, logout, retry }), [state, login, register, logout, retry]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth doit être utilisé dans un AuthProvider");
  return value;
}
