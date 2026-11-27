import { useEffect, useReducer, useState } from "react";
import { useAuth } from "./session";
import type { History, Version } from "./types";

export function useQuizRecords(
  documentId: string,
  quizId: string,
  documentStatus: string | undefined,
  onError: (message: string) => void,
) {
  const { request } = useAuth();
  const [versions, setVersions] = useState<Version[]>([]);
  const [record, setRecord] = useState<{ quizId: string; items: History[] }>();
  const [revision, refreshHistory] = useReducer((value: number) => value + 1, 0);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    request<Version[]>("/documents/" + documentId + "/quizzes", { signal })
      .then((items) => { if (!signal.aborted) setVersions(items); })
      .catch((error) => { if (!signal.aborted) onError(error.message); });
    return () => controller.abort();
  }, [documentId, documentStatus, request, onError]);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    request<History[]>("/quizzes/" + quizId + "/attempts", { signal })
      .then((items) => { if (!signal.aborted) setRecord({ quizId, items }); })
      .catch((error) => { if (!signal.aborted) onError(error.message); });
    return () => controller.abort();
  }, [quizId, request, onError, revision]);

  return { versions, history: record?.quizId === quizId ? record.items : [], refreshHistory };
}
