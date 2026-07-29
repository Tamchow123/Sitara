import type { Metadata } from "next";
import { Caprasimo, Cormorant_Garamond, Figtree } from "next/font/google";
import { Providers } from "./providers";
import "./globals.css";

// The three faces of the handoff's "Organic" design system.
//
// next/font downloads and SELF-HOSTS these at build time, serving them from
// our own origin. That is deliberate: the handoff stylesheet pulls them from
// fonts.googleapis.com, which our `font-src 'self'` CSP (see
// lib/security-headers.ts) forbids — and a third-party font request would
// also leak a visitor's IP to Google on every page load. Only the weights the
// design actually uses are requested. `display: "swap"` keeps text readable
// while the face loads instead of blocking on it.
//
// Each exposes a CSS custom property consumed by the token block at the top
// of globals.css; nothing outside that block names a font family directly.

// Page h1s and the Sitara wordmark.
const cormorant = Cormorant_Garamond({
  subsets: ["latin"],
  weight: ["400", "600"],
  display: "swap",
  variable: "--font-cormorant",
});

// Question legends, option-card titles and drawer headings.
const caprasimo = Caprasimo({
  subsets: ["latin"],
  weight: "400",
  display: "swap",
  variable: "--font-caprasimo",
});

// Body copy, helper text, labels and controls.
const figtree = Figtree({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  display: "swap",
  variable: "--font-figtree",
});

export const metadata: Metadata = {
  title: "Sitara",
  description:
    "AI-assisted South Asian bridalwear concept design — concept visualisation only.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${cormorant.variable} ${caprasimo.variable} ${figtree.variable}`}
    >
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
