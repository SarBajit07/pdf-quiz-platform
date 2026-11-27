import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "QuillQuiz — Read closely. Remember deeply.",
  description: "Turn your PDF notes and exam papers into practice quizzes.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
