"use client";

// A QR code, rendered as our own inline SVG (Phase 22, ADR 0026).
//
// `uqr` encodes; this file draws. That split is deliberate: the encoder is a
// zero-dependency 79 KB module doing the one thing we cannot reasonably write
// ourselves (Reed–Solomon, mask selection, the format bits), and the drawing
// stays here where the contrast, the quiet zone and the accessible name are
// the repository's own decisions rather than a library's defaults.
//
// Nothing about the encoded value is logged, persisted or sent anywhere. It
// arrives as a prop, becomes path geometry, and dies with the component.

import { useId, useMemo } from "react";
import { encode } from "uqr";

type Props = {
  /** The URL to encode. Held in memory only — see `lib/api.ts`. */
  value: string;
  /** Rendered edge length in CSS pixels. */
  size?: number;
  /** The accessible name. Never the URL: a screen reader reading out a bearer
   *  credential character by character helps nobody and leaks it aloud. */
  label: string;
};

// Four modules of quiet zone is the spec's minimum, and shop lighting plus a
// glossy iPad screen is exactly the condition where scanners need it.
const QUIET_ZONE = 4;

export function QrCode({ value, size = 240, label }: Props) {
  const titleId = useId();

  const { path, extent } = useMemo(() => {
    // Error correction M: enough redundancy to survive a fingerprint or a
    // reflection on the screen, without inflating the module count so far that
    // an older phone camera cannot resolve it at arm's length.
    const result = encode(value, { ecc: "M" });
    const total = result.size + QUIET_ZONE * 2;
    // One <path> of many little squares rather than thousands of <rect>
    // elements: an old phone rendering this page has to lay it out too.
    const segments: string[] = [];
    for (let row = 0; row < result.size; row += 1) {
      for (let column = 0; column < result.size; column += 1) {
        if (result.data[row]?.[column]) {
          segments.push(`M${column + QUIET_ZONE} ${row + QUIET_ZONE}h1v1h-1z`);
        }
      }
    }
    return { path: segments.join(""), extent: total };
  }, [value]);

  return (
    <svg
      className="qr-code"
      width={size}
      height={size}
      viewBox={`0 0 ${extent} ${extent}`}
      role="img"
      aria-labelledby={titleId}
      // Crisp module edges at any scale; a smoothed QR scans worse.
      shapeRendering="crispEdges"
    >
      <title id={titleId}>{label}</title>
      {/* An explicit light plate, not the page's ground. A QR inherits
          nothing: it needs a light quiet zone and dark modules whatever theme
          the shell is in, or it stops being scannable in dark mode. The two
          tokens are declared in `tokens.css` and are the one pair documented
          as deliberately not theme-responsive. */}
      <rect width={extent} height={extent} fill="var(--color-qr-light)" />
      <path d={path} fill="var(--color-qr-dark)" />
    </svg>
  );
}
