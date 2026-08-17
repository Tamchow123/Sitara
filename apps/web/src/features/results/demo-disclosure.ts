// The single source of truth for the demo result-disclosure sentence (Phase
// 15 spec §37), shared verbatim between the HTML result view (DesignBrief)
// and the plain-text copy/download brief (result-brief) so the two can never
// drift out of sync.
export const DEMO_RESULT_DISCLOSURE =
  "This visual was selected from Sitara's curated demo pack to resemble your choices. " +
  "It was not newly generated for this design and may not reflect every detail in the brief.";

// Shown ONLY when a demo refinement resolved to the very same pack image as the
// concept it was refined from (ADR 0028 §8). A legitimate outcome of a small
// reviewed pack — and exactly the case ADR 0016's honesty rule says must be
// stated rather than glossed over, because silently returning the same picture
// is what produced the "refinement changes nothing" report in the first place.
// Kept beside the sentence above, and used by both the HTML view and the
// plain-text brief, for the same reason: the two must never drift.
export const DEMO_REFINEMENT_SAME_ASSET_DISCLOSURE =
  "Your refinement did change the design brief, but the demo pack had no closer image, " +
  "so the same visual was selected again. In live generation this would be a new image.";
