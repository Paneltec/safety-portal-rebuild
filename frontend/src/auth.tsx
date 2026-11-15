import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, clearToken, getToken, setToken, type Me, type TokenResponse } from "./api";

interface AuthState {
  status: "loading" | "anonymous" | "signed-in";
  me: Me | null;
  notice: string | null;
  signIn: (email: string, password: string, remember: boolean) => Promise<void>;
  adoptToken: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
  reload: () => Promise<void>;
  can: (resource: string, action: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!getToken()) {
      setMe(null);
      setStatus("anonymous");
      return;
    }
    try {
      setMe(await api<Me>("/api/auth/me"));
      setStatus("signed-in");
    } catch {
      setMe(null);
      setStatus("anonymous");
    }
  }, []);

  useEffect(() => {
    void reload();
    const onSignedOut = (event: Event) => {
      setMe(null);
      setStatus("anonymous");
      setNotice((event as CustomEvent<string>).detail || "Please sign in again.");
    };
    window.addEventListener("sp:signed-out", onSignedOut);
    return () => window.removeEventListener("sp:signed-out", onSignedOut);
  }, [reload]);

  const value = useMemo<AuthState>(
    () => ({
      status,
      me,
      notice,
      async signIn(email, password, remember) {
        const result = await api<TokenResponse>("/api/auth/login", {
          method: "POST",
          body: { email, password, remember_me: remember },
          anonymous: true,
        });
        setToken(result.access_token, remember);
        setNotice(null);
        await reload();
      },
      async adoptToken(token) {
        setToken(token, false);
        setNotice(null);
        await reload();
      },
      async signOut() {
        try {
          await api("/api/auth/logout", { method: "POST" });
        } catch {
          // Signing out locally is still correct if the server cannot be reached.
        }
        clearToken();
        setMe(null);
        setStatus("anonymous");
      },
      reload,
      can: (resource, action) => me?.effective_permissions?.[resource]?.[action] === true,
    }),
    [status, me, notice, reload],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
