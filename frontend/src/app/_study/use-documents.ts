import { useEffect, useState, useCallback, useRef } from "react";
import { useAuth } from "./session";
import type { Doc } from "./types";
export function useDocuments(setError: (error: string) => void) {
  const { request } = useAuth();
  const sequence = useRef(0);
  const [docs, setDocs] = useState<Doc[]>([]);
  const hasActiveDocuments = docs.some((d) => ["queued", "processing"].includes(d.status));
  const load = useCallback(async () => {
    const id = ++sequence.current;
    try {
      const documents = await request<Doc[]>("/documents");
      if (sequence.current === id) setDocs(documents);
    } catch (error) {
      if (sequence.current === id) setError((error as Error).message);
    }
  }, [request, setError]);
  useEffect(() => {
    const timer = window.setTimeout(load, 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    if (!hasActiveDocuments) return;
    let cancelled = false;
    const poll = async () => {
      await load();
      if (!cancelled) timer = window.setTimeout(poll, 3000);
    };
    let timer = window.setTimeout(poll, 3000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [hasActiveDocuments, load]);
  return { docs, load };
}
