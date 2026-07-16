"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { useAuth } from "@/lib/auth";
import { DEFAULT_AUTHENTICATED_PATH } from "@/lib/navigation";

type FieldErrors = Record<string, string[]>;

function FieldError({ id, errors }: { id: string; errors?: string[] }) {
  if (!errors?.length) return null;
  return (
    <ul id={id} role="alert" className="status-bad field-errors">
      {errors.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
}

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setFieldErrors({});
    try {
      const result = await register(email, password, passwordConfirm);
      if (result.ok) {
        router.push(DEFAULT_AUTHENTICATED_PATH);
        return;
      }
      setFieldErrors(result.fields ?? {});
      setError(
        result.code === "auth_rate_limited"
          ? "Too many attempts. Please wait a while and try again."
          : result.message,
      );
    } catch {
      setError("The service could not be reached. Please try again shortly.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main>
      <h1>Create account</h1>
      <p className="tagline">
        An account keeps your bridal concepts private to you.
      </p>
      <section aria-labelledby="register-heading">
        <h2 id="register-heading">Your details</h2>
        <form onSubmit={onSubmit} noValidate>
          <div className="field">
            <label htmlFor="email">Email address</label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              required
              aria-describedby="email-errors"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
            <FieldError id="email-errors" errors={fieldErrors.email} />
          </div>
          <div className="field">
            <label htmlFor="password">Password (at least 12 characters)</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="new-password"
              required
              aria-describedby="password-errors"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <FieldError id="password-errors" errors={fieldErrors.password} />
          </div>
          <div className="field">
            <label htmlFor="password-confirm">Confirm password</label>
            <input
              id="password-confirm"
              name="password_confirm"
              type="password"
              autoComplete="new-password"
              required
              aria-describedby="password-confirm-errors"
              value={passwordConfirm}
              onChange={(event) => setPasswordConfirm(event.target.value)}
            />
            <FieldError
              id="password-confirm-errors"
              errors={fieldErrors.password_confirm}
            />
          </div>
          {error && (
            <p role="alert" className="status-bad">
              {error}
            </p>
          )}
          <button type="submit" disabled={submitting}>
            {submitting ? "Creating account…" : "Create account"}
          </button>
        </form>
        <p>
          Already have an account? <Link href="/login">Sign in</Link>
        </p>
      </section>
    </main>
  );
}
