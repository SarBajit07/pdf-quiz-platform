import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, useWatch } from "react-hook-form";
import { uploadSchema } from "./form-schemas";
import { useAuth } from "./session";
import { useAction } from "./use-action";
import { Notice } from "./notice";
export function UploadForm({ load }: { load: () => Promise<void> }) {
  const { request } = useAuth();
  const { busy, run, error, setError } = useAction();
  const {
    register,
    handleSubmit,
    control,
    resetField,
    formState: { errors },
  } = useForm({
    resolver: zodResolver(uploadSchema),
    defaultValues: {
      doc_type: "auto",
      num_questions: 10,
      difficulty: "medium",
    },
  });
  const file = useWatch({ control, name: "file" })?.item(0);
  const upload = handleSubmit(async (values) => {
    await run(async () => {
      const body = new FormData();
      body.append("file", values.file);
      body.append("doc_type", values.doc_type);
      body.append("num_questions", String(values.num_questions));
      body.append("difficulty", values.difficulty);
      await request("/documents", { method: "POST", body });
      resetField("file");
      await load();
    });
  });
  return (
    <>
      <form className="upload panel" noValidate onSubmit={upload}>
        <div>
          <span className="upload-icon">↥</span>
          <h3>Bring your material in.</h3>
          <p>Notes become new questions. Exam papers become practice sets.</p>
        </div>
        <label className="file">
          {file ? file.name : "Choose a PDF"}
          <input
            type="file"
            accept="application/pdf,.pdf"
            {...register("file")}
            aria-invalid={!!errors.file}
            aria-describedby={errors.file ? "upload-file-error" : undefined}
          />
          {errors.file && (
            <span className="warning" id="upload-file-error" role="alert">
              {errors.file.message}
            </span>
          )}
        </label>
        <div className="fields">
          <label>
            Document type
            <select
              {...register("doc_type")}
              aria-invalid={!!errors.doc_type}
              aria-describedby={
                errors.doc_type ? "upload-type-error" : undefined
              }
            >
              <option value="auto">Detect automatically</option>
              <option value="notes">Study notes</option>
              <option value="exam">Exam paper</option>
            </select>
            {errors.doc_type && (
              <span className="warning" id="upload-type-error" role="alert">
                {errors.doc_type.message}
              </span>
            )}
          </label>
          <label>
            Questions
            <input
              type="number"
              min="5"
              max="30"
              {...register("num_questions", { valueAsNumber: true })}
              aria-invalid={!!errors.num_questions}
              aria-describedby={
                errors.num_questions ? "upload-count-error" : undefined
              }
            />
            {errors.num_questions && (
              <span className="warning" id="upload-count-error" role="alert">
                {errors.num_questions.message}
              </span>
            )}
          </label>
          <label>
            Difficulty
            <select
              {...register("difficulty")}
              aria-invalid={!!errors.difficulty}
              aria-describedby={
                errors.difficulty ? "upload-difficulty-error" : undefined
              }
            >
              <option>easy</option>
              <option>medium</option>
              <option>hard</option>
            </select>
            {errors.difficulty && (
              <span
                className="warning"
                id="upload-difficulty-error"
                role="alert"
              >
                {errors.difficulty.message}
              </span>
            )}
          </label>
        </div>
        <button className="primary" disabled={busy}>
          Create my quiz <span>↗</span>
        </button>
      </form>
      <Notice message={error} dismiss={() => setError("")} />
    </>
  );
}
