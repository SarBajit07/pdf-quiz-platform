import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { registrationSchema, signInSchema } from "./form-schemas";
import { useAuth, API } from "./session";
import { useAction } from "./use-action";
import { Notice } from "./notice";
export function AuthForm() {
  const { saveTokens } = useAuth();
  const { busy, error, setError, run } = useAction();
  const [isRegistering, setIsRegistering] = useState(false);
  const {
    register,
    handleSubmit,
    clearErrors,
    formState: { errors },
  } = useForm({
    resolver: zodResolver(isRegistering ? registrationSchema : signInSchema),
    defaultValues: { email: "", password: "" },
  });
  const auth = handleSubmit(async (credentials) => {
    await run(async () => {
      if (isRegistering) {
        const created = await fetch(API + "/auth/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(credentials),
        });
        if (!created.ok) {
          if (created.status === 422) throw new Error("Check your email address and password.");
          const data = await created.json();
          throw new Error(data.detail ?? "Registration failed");
        }
      }
      const response = await fetch(API + "/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(credentials),
      });
      if (!response.ok) {
        if (response.status === 422) throw new Error("Check your email address and password.");
        const data = await response.json();
        throw new Error(data.detail ?? "Sign in failed");
      }
      const pair = await response.json();
      saveTokens(pair.access_token, pair.refresh_token);
    });
  });
  return (
    <>
      <div className="entry">
        <section className="hero">
          <p className="eyebrow">THE STUDY DESK / 01</p>
          <h1>
            Your notes,
            <br />
            <em>questioned.</em>
          </h1>
          <p>
            Upload a PDF. Practice with questions grounded in its pages. Find
            out what you really know.
          </p>
          <div className="process">
            UPLOAD <span>→</span> PRACTICE <span>→</span> REMEMBER
          </div>
        </section>
        <section className="panel auth">
          <p className="eyebrow">WELCOME IN</p>
          <h2>
            {isRegistering
              ? "Make room to learn."
              : "Pick up where you left off."}
          </h2>
          <form noValidate onSubmit={auth}>
            <label>
              Email
              <input
                type="email"
                autoComplete="email"
                {...register("email")}
                aria-invalid={!!errors.email}
                aria-describedby={errors.email ? "auth-email-error" : undefined}
                placeholder="you@example.com"
              />
              {errors.email && (
                <span className="warning" id="auth-email-error" role="alert">
                  {errors.email.message}
                </span>
              )}
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete={
                  isRegistering ? "new-password" : "current-password"
                }
                {...register("password")}
                aria-invalid={!!errors.password}
                aria-describedby={
                  errors.password ? "auth-password-error" : undefined
                }
                placeholder="Your password"
              />
              {errors.password && (
                <span className="warning" id="auth-password-error" role="alert">
                  {errors.password.message}
                </span>
              )}
            </label>
            <button className="primary" disabled={busy}>
              {isRegistering ? "Create account" : "Sign in"} <span>↗</span>
            </button>
          </form>
          <button
            className="link"
            disabled={busy}
            onClick={() => {
              clearErrors();
              setError("");
              setIsRegistering(!isRegistering);
            }}
          >
            {isRegistering
              ? "Already have an account? Sign in"
              : "New here? Create an account"}
          </button>
        </section>
      </div>
      <Notice message={error} dismiss={() => setError("")} />
    </>
  );
}
