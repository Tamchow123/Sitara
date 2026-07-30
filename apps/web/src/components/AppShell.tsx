"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { BrandLockup } from "@/components/BrandLockup";
import { useAuth } from "@/lib/auth";

// The one branded frame every route renders inside.
//
// Before Phase 17 each page opened its own bare <main> and there was no header
// at all, so the brand, the way back to the start and the skip link existed on
// no screen. This component owns all three, which is why routes hand it their
// content rather than wrapping themselves: a page that forgets the shell is
// visibly wrong, where a page that forgot to repeat a copied header was not.
//
// It is a client component because the account link reflects live session
// state. Nothing here is an authorisation boundary — Django ownership checks
// are (CLAUDE.md §10).

type ShellWidth = "default" | "wide" | "narrow";

const WIDTH_CLASS: Record<ShellWidth, string> = {
  // The three container widths the handoff actually uses.
  default: "container",
  wide: "container container-wide",
  narrow: "container container-narrow",
};

export type AppShellProps = {
  children: ReactNode;
  /** Container width for both the header and the main column. */
  width?: ShellWidth;
  /** Extra classes for the <main> element itself. */
  mainClassName?: string;
  /**
   * A short reassurance shown beside the brand on journeys where leaving the
   * page could be misread as abandoning work — the questionnaire (the draft is
   * saved) and generation (the job keeps running). Plain copy, never a
   * private identifier.
   */
  homeHint?: string;
  /** Route-specific header controls, rendered after the brand lockup. */
  actions?: ReactNode;
  /** Set on the landing page, which introduces the account links itself. */
  hideAccountNav?: boolean;
};

function AccountNav() {
  const { status, user } = useAuth();

  // `loading` and `unavailable` render nothing rather than a spinner: the
  // header is not where a session check should announce itself, and a control
  // that appears a moment later is less disruptive than one that changes label.
  if (status === "authenticated" && user) {
    return (
      <nav aria-label="Account" className="shell-account">
        <Link href="/account" className="btn btn-ghost">
          Your account
        </Link>
      </nav>
    );
  }
  if (status === "anonymous") {
    return (
      <nav aria-label="Account" className="shell-account">
        <Link href="/login" className="btn btn-ghost">
          Sign in
        </Link>
        <Link href="/register" className="btn btn-secondary">
          Create account
        </Link>
      </nav>
    );
  }
  return null;
}

export function AppShell({
  children,
  width = "default",
  mainClassName,
  homeHint,
  actions,
  hideAccountNav = false,
}: AppShellProps) {
  const container = WIDTH_CLASS[width];
  return (
    <div className="shell">
      {/* First focusable element in the document, ahead of the lockup. */}
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className={`shell-header ${container}`}>
        <BrandLockup />
        {homeHint ? <p className="shell-home-hint">{homeHint}</p> : null}
        <div className="shell-actions">
          {actions}
          {hideAccountNav ? null : <AccountNav />}
        </div>
      </header>
      <main id="main-content" className={[container, mainClassName].filter(Boolean).join(" ")}>
        {children}
      </main>
      <footer className={`shell-footer ${container}`}>
        <p>
          Sitara produces <strong>concept visualisations only</strong> — not sewing patterns,
          and not a guarantee that a garment can be made exactly as shown.
        </p>
        <p>Your designs are private by default.</p>
        {/* The two sentences above are the short version; these are where the
            long version lives. Both are static content pages, so they are safe
            to reach from every screen including mid-questionnaire. */}
        <nav aria-label="About Sitara" className="shell-footer-links">
          <Link href="/privacy">Privacy</Link>
          <Link href="/concepts">About concepts</Link>
        </nav>
      </footer>
    </div>
  );
}
