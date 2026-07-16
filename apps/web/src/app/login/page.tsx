"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { useAuth } from "@/lib/auth";
import { safeNextPath } from "@/lib/navigation";

function errorMessageFor(code: string, fallback: string): string {
  if (code === "auth_rate_limited") {
    return "Too many attempts. Please wait a few minutes and try again.";
  }
  if (code === "unavailable" || code === "auth_unavailable") {
    return "Sign-in is temporarily unavailable. Please try again shortly.";
  }
  return fallback;
}

function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await login(email, password);
      if (result.ok) {
        router.push(safeNextPath(searchParams.get("next")));
        return;
      }
      setError(errorMessageFor(result.code, result.message));
    } catch {
      setError("The service could not be reached. Please try again shortly.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main>
      <h1>Sign in</h1>
      <p className="tagline">Welcome back to Sitara.</p>
      <section aria-labelledby="login-heading">
        <h2 id="login-heading">Your account</h2>
        <form onSubmit={onSubmit} noValidate>
          <div className="field">
            <label htmlFor="email">Email address</label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          {error && (
            <p role="alert" className="status-bad">
              {error}
            </p>
          )}
          <button type="submit" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p>
          New to Sitara? <Link href="/register">Create an account</Link>
        </p>
      </section>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
