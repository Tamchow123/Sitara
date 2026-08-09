// The one glyph two modules both need.
//
// The rail and the empty state each draw a pin, at different sizes. They were
// separate copies of the same path, which is the kind of duplication that ends
// with two pins that no longer look like the same pin. The rest of the rail's
// glyphs are used in exactly one place and stay there — a shared module for
// single-use icons would be indirection for its own sake.

export function PinGlyph({ size = 18 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <path
        d="M12 2a7 7 0 0 0-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 0 0-7-7Zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5Z"
        fill="currentColor"
      />
    </svg>
  );
}
