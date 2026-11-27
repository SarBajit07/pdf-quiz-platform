import { z } from "zod";

export const signInSchema = z.object({
  email: z
    .string()
    .trim()
    .includes("@", { error: "Enter a valid email address." }),
  password: z.string().min(1, "Enter your password."),
});

export const registrationSchema = signInSchema.extend({
  password: z.string().refine((password) => {
    const length = Array.from(password).length;
    return length >= 8 && length <= 128;
  }, "Use 8 to 128 characters."),
});

export const uploadSchema = z.object({
  file: z
    .custom<FileList>(
      (value) => typeof FileList !== "undefined" && value instanceof FileList,
      {
        error: "Choose a PDF.",
      },
    )
    .transform((files) => files.item(0))
    .pipe(
      z.file({ error: "Choose a PDF." }).mime("application/pdf", "Choose a PDF file."),
    ),
  doc_type: z.enum(["auto", "notes", "exam"], {
    error: "Select a document type.",
  }),
  num_questions: z
    .number({ error: "Enter a question count." })
    .int("Enter a whole number.")
    .min(5, "Enter at least 5 questions.")
    .max(30, "Enter no more than 30 questions."),
  difficulty: z.enum(["easy", "medium", "hard"], {
    error: "Select a difficulty.",
  }),
});
