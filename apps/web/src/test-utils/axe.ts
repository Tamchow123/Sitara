// The one place the automated accessibility check is configured (Phase 17 §24).
//
// Every axe run in this repository goes through `axeViolations`, so the rule
// set cannot drift between test files: a change here applies to all of them,
// and a new test file gets the agreed configuration by importing one function
// rather than copying a config object it might copy wrong.
//
// `toHaveNoViolations` is registered once in vitest.setup.ts, so tests only
// need this import.

import { axe } from "jest-axe";

// colour-contrast is the one rule deliberately disabled. jsdom performs no
// layout and resolves no cascade, so axe cannot measure a real contrast ratio
// here and would report noise either way. Contrast is covered properly by
// app/design-tokens.test.ts, which reads the actual stylesheet and recomputes
// the WCAG 2.x ratios against the documented contract. Nothing else is
// disabled — a violation from any other rule is a real finding.
const AXE_CONFIG = { rules: { "color-contrast": { enabled: false } } };

/**
 * Run axe over a rendered container with the repository's agreed rule set.
 *
 * Pair it with the registered matcher:
 *   expect(await axeViolations(container)).toHaveNoViolations();
 *
 * Call it only once the state under test has actually rendered — awaiting a
 * `findBy*` first — or it audits the loading state instead of the target.
 */
export async function axeViolations(container: Element) {
  return axe(container, AXE_CONFIG);
}
