"use client";
import {
  createContext,
  useContext,
  useRef,
  useState,
  useCallback,
  useEffect,
  ReactNode,
} from "react";
export const API =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
type Pair = { access_token: string; refresh_token: string };
type Session = {
  signedIn: boolean;
  saveTokens: (access: string, refresh: string) => void;
  logout: () => void;
  request: <T>(path: string, options?: RequestInit) => Promise<T>;
};
const Context = createContext<Session | null>(null);
export function useAuth() {
  const auth = useContext(Context);
  if (!auth) throw new Error("Missing SessionProvider");
  return auth;
}
export function SessionProvider({ children }: { children: ReactNode }) {
  const tokens = useRef<Pair | null>(null);
  const pending = useRef<Promise<Pair> | null>(null);
  const generation = useRef(0);
  const [signedIn, setSignedIn] = useState(false);
  const logout = useCallback(() => {
    generation.current++;
    tokens.current = null;
    pending.current = null;
    sessionStorage.removeItem("quiz-access");
    sessionStorage.removeItem("quiz-refresh");
    setSignedIn(false);
  }, []);
  const saveTokens = useCallback((access: string, refresh: string) => {
    tokens.current = { access_token: access, refresh_token: refresh };
    sessionStorage.setItem("quiz-access", access);
    sessionStorage.setItem("quiz-refresh", refresh);
    setSignedIn(true);
  }, []);
  useEffect(() => {
    const a = sessionStorage.getItem("quiz-access"),
      r = sessionStorage.getItem("quiz-refresh");
    if (a && r) queueMicrotask(() => saveTokens(a, r));
  }, [saveTokens]);
  const request = useCallback(
    async <T,>(path: string, options: RequestInit = {}): Promise<T> => {
      const original = tokens.current;
      const session = generation.current;
      if (!original) throw new Error("Please sign in.");
      const send = (pair: Pair) =>
        fetch(API + path, {
          ...options,
          headers: {
            ...(options.body instanceof FormData
              ? {}
              : { "Content-Type": "application/json" }),
            ...options.headers,
            Authorization: "Bearer " + pair.access_token,
          },
        });
      let response = await send(original);
      if (response.status === 401 && generation.current === session) {
        if (tokens.current === original) {
          pending.current ??= (async () => {
            const renewed = await fetch(API + "/auth/refresh", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ refresh_token: original.refresh_token }),
            });
            if (generation.current !== session)
              throw new Error("Session changed.");
            if (!renewed.ok) {
              if (renewed.status === 401) logout();
              throw new Error("Unable to refresh your session.");
            }
            const pair: Pair = await renewed.json();
            if (generation.current !== session)
              throw new Error("Session changed.");
            saveTokens(pair.access_token, pair.refresh_token);
            return pair;
          })().finally(() => {
            if (generation.current === session) pending.current = null;
          });
          await pending.current;
        }
        if (generation.current !== session || !tokens.current)
          throw new Error("Session changed.");
        response = await send(tokens.current);
      }
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "Request failed (" + response.status + ")",
        );
      }
      return response.status === 204 ? (undefined as T) : response.json();
    },
    [logout, saveTokens],
  );
  return (
    <Context.Provider value={{ signedIn, saveTokens, logout, request }}>
      {children}
    </Context.Provider>
  );
}
