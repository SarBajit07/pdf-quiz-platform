import { useReducer } from "react";
import type { Quiz, Attempt } from "./types";
import { useAuth } from "./session";
import { useAction } from "./use-action";
import { useQuizRecords } from "./use-quiz-records";
import { Notice } from "./notice";
type State = {
  quiz: Quiz;
  answers: Record<string, number>;
  attempt: Attempt | null;
};
type Action =
  | { type: "open"; quiz: Quiz }
  | { type: "answer"; id: string; index: number }
  | { type: "result"; attempt: Attempt }
  | { type: "retake" };
function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "open":
      return { quiz: action.quiz, answers: {}, attempt: null };
    case "answer":
      return {
        ...state,
        answers: { ...state.answers, [action.id]: action.index },
      };
    case "retake":
      return { ...state, attempt: null, answers: {} };
    case "result": {
      const answers: Record<string, number> = {};
      for (const result of action.attempt.results) {
        if (result.selected_index !== null)
          answers[result.question_id] = result.selected_index;
      }
      return { ...state, answers, attempt: action.attempt };
    }
  }
}
export function QuizSession({
  initial,
  documentStatus,
  onBack,
  reload,
}: {
  initial: Quiz;
  documentStatus: string | undefined;
  onBack: () => void;
  reload: () => Promise<void>;
}) {
  const [{ quiz, attempt, answers }, dispatch] = useReducer(reducer, {
    quiz: initial,
    attempt: null,
    answers: {},
  });
  const { request } = useAuth();
  const { run, busy, error, setError } = useAction();
  const { versions, history, refreshHistory } = useQuizRecords(
    quiz.document_id,
    quiz.id,
    documentStatus,
    setError,
  );
  const openVersion = (id: string) =>
    void run(async () => {
      const version = await request<Quiz>("/quizzes/" + id);
      dispatch({ type: "open", quiz: version });
    });
  const mcqs = quiz.questions.filter((q) => q.question_type === "mcq");
  const submit = () =>
    void run(async () => {
      const result = await request<Attempt>(
        "/quizzes/" + quiz.id + "/attempts",
        {
          method: "POST",
          body: JSON.stringify({
            answers: mcqs.map((q) => ({
              question_id: q.id,
              selected_index: answers[q.id],
            })),
          }),
        },
      );
      dispatch({ type: "result", attempt: result });
      refreshHistory();
    });
  const review = (id: string) =>
    void run(async () => {
      dispatch({
        type: "result",
        attempt: await request<Attempt>(
          "/quizzes/" + quiz.id + "/attempts/" + id,
        ),
      });
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  const regenerate = () =>
    void run(async () => {
      await request("/quizzes/" + quiz.id + "/regenerate", { method: "POST" });
      onBack();
      await reload();
    });
  return (
    <>
      {versions.length > 1 && (
        <nav className="versions" aria-label="Quiz versions">
          <span>Quiz versions</span>
          {versions.map((version, index) => (
            <button
              key={version.id}
              className={quiz.id === version.id ? "active" : ""}
              onClick={() => openVersion(version.id)}
              disabled={busy}
            >
              {index === 0
                ? "Latest"
                : new Date(version.created_at).toLocaleDateString()}{" "}
              · {version.num_questions} questions
            </button>
          ))}
        </nav>
      )}
      <>
        <button className="link" onClick={onBack}>
          ← Back to library
        </button>
        <div className="heading">
          <div>
            <p className="eyebrow">PRACTICE SESSION</p>
            <h2>{quiz.title}</h2>
            <p>
              {mcqs.length} questions · {quiz.questions.length - mcqs.length}{" "}
              flashcards
            </p>
          </div>
          <button className="outline" disabled={busy} onClick={regenerate}>
            Generate new quiz
          </button>
        </div>
        {attempt && (
          <div className="score">
            <small>YOUR RESULT</small>
            <b>
              {attempt.score}
              <span> / {attempt.total}</span>
            </b>
            <button
              className="outline"
              onClick={() => dispatch({ type: "retake" })}
            >
              Retake quiz
            </button>
          </div>
        )}
        {quiz.questions.map((q) => {
          const result = attempt?.results.find((r) => r.question_id === q.id);
          return (
            <article className="question" key={q.id}>
              <div className="meta">
                <span>QUESTION {String(q.position).padStart(2, "0")}</span>
                <span>PAGE {q.source_page}</span>
              </div>
              <h3>{q.text}</h3>
              {q.answer_source === "ai_solved" && (
                <p className="warning">
                  AI-solved answer. Verify against the source.
                </p>
              )}
              {q.flagged && (
                <p className="warning">
                  Flagged for review: answer checks may disagree.
                </p>
              )}
              {q.options ? (
                <div className="options">
                  {q.options.map((option, i) => {
                    const selected = answers[q.id] === i;
                    const correct = result?.correct_index === i;
                    const className = "option" + (selected ? " selected" : "") +
                      (correct ? " correct" : "");
                    const letter = String.fromCharCode(65 + i);
                    return result ? (
                      <div key={i} className={className}>
                        <b>{letter}</b>
                        <div>
                          {option}
                          {selected && <div><small>Your answer</small></div>}
                          {correct && <div><small>Correct answer</small></div>}
                        </div>
                      </div>
                    ) : (
                      <button
                        key={i}
                        disabled={busy}
                        aria-pressed={selected}
                        className={className}
                        onClick={() => dispatch({ type: "answer", id: q.id, index: i })}
                      >
                        <b>{letter}</b>
                        {option}
                      </button>
                    );
                  })}
                </div>
              ) : !result && (
                <p className="flashcard">
                  {q.has_model_answer
                    ? "Think through your answer, then reveal the model answer in the results."
                    : `Self-study prompt. Use page ${q.source_page} of the PDF to review your answer.`}
                </p>
              )}
              {result && (
                <div className="explanation">
                  <b>
                    {result.correct === null
                      ? q.has_model_answer ? "Model answer" : "Self-study prompt"
                      : result.correct
                        ? "Correct"
                        : "Review this one"}
                  </b>
                  {q.has_model_answer && <p>{result.model_answer}</p>}
                  {result.correct === null && !q.has_model_answer && (
                    <p>Use the source page to review your answer.</p>
                  )}
                  {result.explanation && <p>{result.explanation}</p>}
                  <small>Source: page {result.source_page}</small>
                </div>
              )}
            </article>
          );
        })}
        {!attempt && (
          <button
            className="primary submit"
            disabled={busy || !mcqs.every((q) => answers[q.id] !== undefined)}
            onClick={submit}
          >
            Submit answers <span>↗</span>
          </button>
        )}
        {history.length > 0 && (
          <section className="history">
            <p className="eyebrow">PAST ATTEMPTS</p>
            {history.map((h) => (
              <div key={h.id}>
                <span>{new Date(h.created_at).toLocaleString()}</span>
                <b>
                  {h.score} / {h.total}
                </b>
                <button
                  className="link"
                  disabled={busy}
                  onClick={() => review(h.id)}
                >
                  Review →
                </button>
              </div>
            ))}
          </section>
        )}
      </>
      <Notice message={error} dismiss={() => setError("")} />
    </>
  );
}
