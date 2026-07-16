"use client";

// Security note: the middleware redirect for this route is a NAVIGATION
// optimisation only. This page always re-verifies the session against
// /api/v1/auth/me/ (via the auth context), and every future design API must
// enforce ownership server-side — Django permissions are the boundary.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

export default function AccountPage() {
  const { status, user, logout } = useAuth();
  const router = useRouter();
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    // Covers stale/expired session cookies that slipped past middleware.
    if (status === "anonymous") {
      router.replace("/login?next=/account");
    }
  }, [status, router]);

  async function onLogout() {
    setSigningOut(true);
    try {
      await logout();
    } finally {
      router.push("/");
    }
  }

  return (
    <main>
      <h1>Your account</h1>
      <section aria-labelledby="account-heading">
        <h2 id="account-heading">Account details</h2>
        <div role="status" aria-live="polite">
          {status === "loading" && <p>Checking your session…</p>}
          {status === "unavailable" && (
            <p className="status-bad">
              Backend unavailable — your account details cannot be loaded right
              now.
            </p>
          )}
          {status === "anonymous" && <p>Redirecting to sign in…</p>}
          {status === "authenticated" && user && (
            <>
              <dl>
                <dt>Email</dt>
                <dd>{user.email}</dd>
              </dl>
              <button type="button" onClick={onLogout} disabled={signingOut}>
                {signingOut ? "Signing out…" : "Sign out"}
              </button>
            </>
          )}
        </div>
      </section>
      <section aria-labelledby="coming-heading">
        <h2 id="coming-heading">What&apos;s next</h2>
        <p>
          Bridal design features — the guided questionnaire, private concept
          generation and your design gallery — arrive in later phases.
        </p>
      </section>
    </main>
  );
}
