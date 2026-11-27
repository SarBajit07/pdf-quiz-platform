"use client";

import { AuthForm } from "./_study/auth-form";
import { SessionProvider, useAuth } from "./_study/session";
import { Workspace } from "./_study/workspace";

function StudyDesk() {
  const { signedIn, logout } = useAuth();
  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <b>Q.</b> Quill<span>Quiz</span>
        </div>
        <small>TURN READING INTO RECALL</small>
        {signedIn && (
          <button className="link" onClick={logout}>
            Sign out ↗
          </button>
        )}
      </header>
      {signedIn ? <Workspace /> : <AuthForm />}
      <footer>
        QUILLQUIZ <span>Read closely. Remember deeply.</span>
      </footer>
    </main>
  );
}

export default function Home() {
  return (
    <SessionProvider>
      <StudyDesk />
    </SessionProvider>
  );
}
