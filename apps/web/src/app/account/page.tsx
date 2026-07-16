"use client";

// Security note: the middleware redirect for this route is a NAVIGATION
// optimisation only. This page always re-verifies the session against
// /api/v1/auth/me/ (via the auth context), and every future design API must
// enforce ownership server-side — Django permissions are the boundary.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

const SIGN_OUT_FAILED_MESSAGE =
  "Sign-out could not be completed. Your session may still be active. " +
  "Please try again.";

export default function AccountPage() {
  const { status, user, logout } = useAuth();
  const router = useRouter();
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);

  useEffect(() => {
    // Covers stale/expired session cookies that slipped past middleware.
    if (status === "anonymous") {
      router.replace("/login?next=/account");
    }
  }, [status, router]);

  // Redirect home ONLY on server-confirmed logout. On any failure the page
  // stays put, keeps showing the (still-authenticated) account details and
  // surfaces an accessible error — never claim a sign-out that Django did
  // not confirm.
  async function onLogout() {
    setSigningOut(true);
    setLogoutError(null);
    try {
      const result = await logout();
      if (result.ok) {
        router.push("/");
        return;
      }
      setLogoutError(SIGN_OUT_FAILED_MESSAGE);
    } catch {
      setLogoutError(SIGN_OUT_FAILED_MESSAGE);
    } finally {
      setSigningOut(false);
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
              {logoutError && (
                <p role="alert" className="status-bad">
                  {logoutError}
                </p>
              )}
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
