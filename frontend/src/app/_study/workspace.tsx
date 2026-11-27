import { useState } from "react";
import { useAuth } from "./session";
import { useDocuments } from "./use-documents";
import { useAction } from "./use-action";
import type { Quiz } from "./types";
import { QuizSession } from "./quiz-session";
import { UploadForm } from "./upload-form";
import { Notice } from "./notice";
export function Workspace() {
  const { request } = useAuth();
  const { busy, error, setError, run } = useAction();
  const { docs, load } = useDocuments(setError);
  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const quizDocumentStatus = docs.find((doc) => doc.id === quiz?.document_id)?.status;
  const open = (id: string) =>
    void run(async () =>
      setQuiz(await request<Quiz>("/documents/" + id + "/quiz")),
    );
  const confirmType = (id: string, selected: string) =>
    void run(async () => {
      await request("/documents/" + id + "/type", {
        method: "PATCH",
        body: JSON.stringify({ doc_type: selected }),
      });
      await load();
    });
  const remove = (id: string) => {
    if (!window.confirm("Delete this document, its quizzes and all attempts?"))
      return;
    void run(async () => {
      await request("/documents/" + id, { method: "DELETE" });
      await load();
    });
  };
  return (
    <>
      <div className="workspace">
        <aside className="side">
          <p className="eyebrow">YOUR WORKSPACE</p>
          <h1>
            A better way
            <br />
            to <em>remember.</em>
          </h1>
          <p>Every page can become a question worth answering.</p>
          <div className="stat">
            <b>{String(docs.length).padStart(2, "0")}</b>
            <span>documents in your library</span>
          </div>
        </aside>
        <div className="content">
          {quiz ? (
            <QuizSession
              initial={quiz}
              documentStatus={quizDocumentStatus}
              onBack={() => setQuiz(null)}
              reload={load}
            />
          ) : (
            <>
              <div className="heading">
                <div>
                  <p className="eyebrow">THE LIBRARY</p>
                  <h2>Start with a document.</h2>
                  <p>Text-based PDFs work best.</p>
                </div>
              </div>
              <UploadForm load={load} />
              <div className="heading library-heading">
                <div>
                  <p className="eyebrow">RECENT WORK</p>
                  <h2>Your library</h2>
                </div>
                <button className="link" onClick={() => void load()}>
                  Refresh ↻
                </button>
              </div>
              {docs.length === 0 ? (
                <p className="empty">
                  Your desk is clear. Upload a PDF to begin.
                </p>
              ) : (
                <div className="documents">
                  {docs.map((d) => (
                    <article className="document" key={d.id}>
                      <div className="pdf">PDF</div>
                      <div className="doc-main">
                        <h3>{d.filename}</h3>
                        <p>
                          {d.page_count || "—"} pages ·{" "}
                          {d.doc_type ?? "detecting type"}
                          {d.generated_question_count !== null
                            ? " · " + d.generated_question_count + " questions"
                            : ""}
                        </p>
                        {d.error_message && (
                          <p className="warning">{d.error_message}</p>
                        )}
                        {(d.status === "needs_confirmation" ||
                          (d.doc_type_source === "auto" && ["ready", "failed"].includes(d.status))) && (
                          <div className="confirm">
                            {d.status === "needs_confirmation" ? "Confirm type" : "Change type"}
                            <button disabled={busy || (d.status === "ready" && d.doc_type === "notes")} onClick={() => confirmType(d.id, "notes")}>
                              Notes
                            </button>
                            <button disabled={busy || (d.status === "ready" && d.doc_type === "exam")} onClick={() => confirmType(d.id, "exam")}>
                              Exam
                            </button>
                          </div>
                        )}
                      </div>
                      <div className="doc-actions">
                        <span className={"status " + d.status}>
                          {d.status.replace("_", " ")}
                        </span>
                        {d.generated_question_count !== null && (
                          <button
                            className="outline"
                            disabled={busy}
                            onClick={() => open(d.id)}
                          >
                            Open quiz →
                          </button>
                        )}
                        <button
                          className="link danger"
                          disabled={d.status === "processing" || busy}
                          onClick={() => remove(d.id)}
                        >
                          Delete
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
      <Notice message={error} dismiss={() => setError("")} />
    </>
  );
}
