"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  fetchPublicConfig,
  fetchReadiness,
  type PublicConfig,
  type ReadyResponse,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

type BackendState =
  | { phase: "loading" }
  | { phase: "unavailable" }
  | { phase: "ready"; readiness: ReadyResponse; config: PublicConfig };

function CheckValue({ value }: { value: string }) {
  const ok = value === "ok";
  return (
    <span className={ok ? "status-ok" : "status-bad"}>
      {ok ? "ok" : "unavailable"}
    </span>
  );
}

function AuthNav() {
  const { status, user } = useAuth();
  return (
    <nav aria-label="Account">
      {status === "authenticated" && user ? (
        <Link href="/account">Account ({user.email})</Link>
      ) : (
        <>
          <Link href="/login">Sign in</Link>{" "}
          <Link href="/register">Create account</Link>
        </>
      )}
    </nav>
  );
}

export default function Home() {
  const [state, setState] = useState<BackendState>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchReadiness(), fetchPublicConfig()])
      .then(([readiness, config]) => {
        if (!cancelled) setState({ phase: "ready", readiness, config });
      })
      .catch(() => {
        if (!cancelled) setState({ phase: "unavailable" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <AuthNav />
      <h1>Sitara</h1>
      <p className="tagline">
        AI-assisted South Asian bridalwear concept design — from questionnaire
        to a private visual concept and detailed design brief.
      </p>

      <section aria-labelledby="platform-status-heading">
        <h2 id="platform-status-heading">Platform status</h2>
        <div role="status" aria-live="polite">
          {state.phase === "loading" && <p>Checking backend status…</p>}
          {state.phase === "unavailable" && (
            <p className="status-bad">
              Backend unavailable — the Sitara API could not be reached.
            </p>
          )}
          {state.phase === "ready" && (
            <dl>
              <dt>Backend connection</dt>
              <dd className="status-ok">connected</dd>
              <dt>Database</dt>
              <dd>
                <CheckValue value={state.readiness.checks.database} />
              </dd>
              <dt>Queue (Redis)</dt>
              <dd>
                <CheckValue value={state.readiness.checks.redis} />
              </dd>
              <dt>Authentication protection</dt>
              <dd>
                <CheckValue value={state.readiness.checks.auth_cache} />
              </dd>
              <dt>Private storage</dt>
              <dd>
                <CheckValue value={state.readiness.checks.storage} />
              </dd>
              <dt>Demo mode</dt>
              <dd>
                <span
                  className={`badge ${state.config.demo_mode ? "status-ok" : "status-bad"}`}
                  aria-label={`Demo mode ${state.config.demo_mode ? "on" : "off"}`}
                >
                  {state.config.demo_mode ? "Demo mode on" : "Demo mode off"}
                </span>
              </dd>
              <dt>Paid generation</dt>
              <dd>
                {state.config.generation_enabled ? "enabled" : "disabled"}
              </dd>
            </dl>
          )}
        </div>
      </section>

      <section aria-labelledby="about-heading">
        <h2 id="about-heading">About this preview</h2>
        <p>
          Sitara currently provides <strong>concept visualisation only</strong>
          . It does not produce sewing patterns and does not guarantee that a
          garment can be constructed exactly as shown.
        </p>
        <p className="notice">
          In demo mode, Sitara makes <strong>no paid AI calls</strong>:
          concepts come from pre-generated private fixtures.
        </p>
      </section>
    </main>
  );
}
