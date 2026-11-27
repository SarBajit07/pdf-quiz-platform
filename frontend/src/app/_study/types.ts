export type Doc = {
  id: string;
  filename: string;
  status: string;
  doc_type: string | null;
  doc_type_source: "auto" | "user";
  page_count: number;
  generated_question_count: number | null;
  error_message: string | null;
};
export type Question = {
  id: string;
  position: number;
  text: string;
  question_type: string;
  options: string[] | null;
  source_page: number;
  answer_source: string;
  flagged: boolean;
  has_model_answer: boolean;
};
export type Quiz = {
  id: string;
  document_id: string;
  title: string;
  questions: Question[];
};
export type Result = {
  question_id: string;
  correct: boolean | null;
  selected_index: number | null;
  correct_index: number | null;
  model_answer: string | null;
  explanation: string | null;
  source_page: number;
};
export type Attempt = { score: number; total: number; results: Result[] };
export type History = {
  id: string;
  score: number;
  total: number;
  created_at: string;
};
export type Version = {
  id: string;
  title: string;
  num_questions: number;
  created_at: string;
};
