import Link from "next/link";

// The handoff's brand lockup: an eight-point star in an accent disc beside the
// Sitara wordmark, set in Cormorant Garamond.
//
// The WHOLE lockup is the link — mark and wordmark together — and it is the
// application's Home action. Its accessible name says so out loud ("Sitara —
// Home") rather than leaving a keyboard or screen-reader user to infer that a
// star glyph means "start again", and the wordmark keeps a visible text label
// beside the icon for everyone else. There is deliberately no second "Home"
// control: two links to `/` in one header would read as two destinations.

export function BrandLockup({ className }: { className?: string }) {
  return (
    <Link
      href="/"
      aria-label="Sitara — Home"
      className={className ? `brand-lockup ${className}` : "brand-lockup"}
    >
      <span className="brand-mark" aria-hidden="true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2 L14.4 9.6 L22 12 L14.4 14.4 L12 22 L9.6 14.4 L2 12 L9.6 9.6 Z" />
        </svg>
      </span>
      <span className="brand-wordmark">Sitara</span>
    </Link>
  );
}
