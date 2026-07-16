"use client";

// Small authentication context over the session-cookie API. No token ever
// touches localStorage/sessionStorage/IndexedDB — the session is an
// HttpOnly cookie and the CSRF token lives in module memory (see api.ts).
//
// IMPORTANT: this is navigation/UX state only. Django endpoint permissions
// remain the security boundary for every API call.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  apiLogin,
  apiLogout,
  apiRegister,
  fetchMe,
  type AuthResult,
  type AuthUser,
} from "@/lib/api";

export type AuthStatus = "loading" | "authenticated" | "anonymous" | "unavailable";

type AuthContextValue = {
  status: AuthStatus;
  user: AuthUser | null;
  login: (email: string, password: string) => Promise<AuthResult>;
  register: (
    email: string,
    password: string,
    passwordConfirm: string,
  ) => Promise<AuthResult>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
};

const defaultValue: AuthContextValue = {
  status: "loading",
  user: null,
  login: async () => ({
    ok: false,
    code: "unavailable",
    message: "Authentication is not ready.",
  }),
  register: async () => ({
    ok: false,
    code: "unavailable",
    message: "Authentication is not ready.",
  }),
  logout: async () => {},
  refreshUser: async () => {},
};

const AuthContext = createContext<AuthContextValue>(defaultValue);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);

  const refreshUser = useCallback(async () => {
    try {
      const me = await fetchMe();
      if (me.authenticated && me.user) {
        setUser(me.user);
        setStatus("authenticated");
      } else {
        // Also covers an expired/stale session cookie.
        setUser(null);
        setStatus("anonymous");
      }
    } catch {
      setUser(null);
      setStatus("unavailable");
    }
  }, []);

  useEffect(() => {
    void refreshUser();
  }, [refreshUser]);

  const login = useCallback(async (email: string, password: string) => {
    const result = await apiLogin(email, password);
    if (result.ok) {
      setUser(result.user);
      setStatus("authenticated");
    }
    return result;
  }, []);

  const register = useCallback(
    async (email: string, password: string, passwordConfirm: string) => {
      const result = await apiRegister(email, password, passwordConfirm);
      if (result.ok) {
        setUser(result.user);
        setStatus("authenticated");
      }
      return result;
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
      setStatus("anonymous");
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{ status, user, login, register, logout, refreshUser }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}
