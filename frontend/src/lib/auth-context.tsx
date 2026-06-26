"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, clearToken, getToken, setToken } from "@/lib/api";
import type { UserOut } from "@/lib/types";

type AuthContextValue = {
  user: UserOut | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string, phone?: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null);
  const [loading, setLoading] = useState(() => getToken() !== null);
  const router = useRouter();

  useEffect(() => {
    if (!getToken()) return;
    api.auth
      .me()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const { access_token } = await api.auth.login(email, password);
      setToken(access_token);
      setUser(await api.auth.me());
      router.push("/");
    },
    [router]
  );

  const register = useCallback(
    async (email: string, password: string, name?: string, phone?: string) => {
      const { access_token } = await api.auth.register(email, password, name, phone);
      setToken(access_token);
      setUser(await api.auth.me());
      router.push("/");
    },
    [router]
  );

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    router.push("/login");
  }, [router]);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
