"use client";

// The landing page, rebuilt from `Sitara Home.dc.html` (Phase 17 Part B).
//
// What used to be here was a development-status dashboard: database, Redis,
// auth-cache and private-storage readiness, a bare "Sitara" heading and no
// imagery. That is operational detail about our infrastructure, shown to a
// bride. It is gone — not restyled — and nothing on this page reports whether
// a backend dependency is up.
//
// The one status that legitimately belongs to a customer is whether the
// concepts she is about to see cost anything to produce and are freshly
// generated, so the demo disclosure stays. When that read fails the page keeps
// working and says so in one bounded line, because a landing page that cannot
// reach an API still has a story to tell and a button that works.

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { fetchPublicConfig, type PublicConfig } from "@/lib/api";
import heroes from "./landing-hero.json";

type ConfigState =
  | { phase: "loading" }
  | { phase: "unavailable" }
  | { phase: "ready"; config: PublicConfig };

const HOW_IT_WORKS = [
  {
    n: "1",
    title: "Tell us your vision",
    body: "Nine guided, visual steps — skip anything you're unsure of. Tap the i on any card to learn an unfamiliar term.",
  },
  {
    n: "2",
    title: "Sitara sketches",
    body: "Your answers become a design brief, and Sitara drafts an original concept that honours your traditions and taste.",
  },
  {
    n: "3",
    title: "Refine it once",
    body: "Ask for one focused change — colour, cloth, coverage or drape — and arrive at a concept you can take to a tailor.",
  },
];

export default function Home() {
  const [state, setState] = useState<ConfigState>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchPublicConfig()
      .then((config) => {
        if (!cancelled) setState({ phase: "ready", config });
      })
      .catch(() => {
        if (!cancelled) setState({ phase: "unavailable" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <AppShell mainClassName="landing">
      <section className="landing-hero screen-enter">
        <div className="landing-hero-copy">
          <p className="kicker">AI-assisted bridalwear concepts</p>
          <h1 className="landing-headline">
            Your bridal vision, <em>sketched by Sitara</em>
          </h1>
          <p className="landing-lede">
            Tell us about your ceremony, your heritage and the details you love — Sitara turns
            your answers into an original bridalwear concept made just for you.
          </p>
          <div className="landing-cta-row">
            <Link className="btn btn-primary btn-large" href="/design/new">
              Start designing
            </Link>
            <span className="landing-cta-note">About 5 minutes · skip anything</span>
          </div>
          <p className="landing-privacy">
            No account needed — your design stays private to you, and nothing you answer or
            upload is ever made public. You can create an account later to keep it.
          </p>
        </div>
        {/* Decorative-but-described: see the alt-text note in
            scripts/build-landing-images.py. The first tile is pushed down to
            give the pair the handoff's asymmetric stagger. */}
        <div className="landing-hero-images">
          {[heroes["hero-1"], heroes["hero-2"]].map((hero) => (
            <div className="landing-hero-tile washed" key={hero.path}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={hero.path}
                alt={hero.alt}
                width={hero.width}
                height={hero.height}
                // The hero is above the fold on every viewport, so it is the
                // one image on the site that must not be lazy.
                loading="eager"
                decoding="async"
              />
            </div>
          ))}
        </div>
      </section>

      <section className="landing-how" aria-labelledby="how-it-works">
        <h2 id="how-it-works">How it works</h2>
        <div className="landing-how-grid">
          {HOW_IT_WORKS.map((step) => (
            <div className="landing-how-card" key={step.n}>
              <span className="landing-how-number" aria-hidden="true">
                {step.n}
              </span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-close" aria-labelledby="landing-close-heading">
        <h2 id="landing-close-heading">Ready to see your vision take shape?</h2>
        <p>
          Answer as much or as little as you like — anything you leave open, Sitara will
          imagine for you.
        </p>
        <Link className="btn btn-primary btn-large" href="/design/new">
          Start designing
        </Link>
      </section>

      <section className="landing-notes" aria-labelledby="landing-notes-heading">
        <h2 id="landing-notes-heading" className="kicker">
          Before you start
        </h2>
        <p>
          Sitara produces a <strong>concept visualisation</strong> — an image and a written
          design brief. It is not a sewing pattern, and it is not a promise that a garment can
          be constructed exactly as shown. Take it to a tailor or bridal designer as a starting
          point, not as a specification.
        </p>
        {/* Honest generation-mode disclosure. Deliberately one line, and never
            a diagnostics panel: loading and unavailable both render at the same
            size so the section cannot shift under the reader. */}
        <p className="landing-mode" role="status" aria-live="polite">
          {state.phase === "loading" && "Checking how concepts are being generated…"}
          {state.phase === "unavailable" &&
            "We could not confirm how concepts are being generated right now. You can still start — your answers are saved as you go."}
          {state.phase === "ready" &&
            (state.config.demo_mode
              ? "Sitara is running in demo mode: concepts come from a small set of pre-generated images, so no new artwork is made for your answers and no AI service is paid to run."
              : "Concepts are generated for your answers when you ask for one.")}
        </p>
      </section>
    </AppShell>
  );
}
