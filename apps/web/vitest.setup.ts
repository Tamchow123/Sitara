import "@testing-library/jest-dom/vitest";

import { toHaveNoViolations } from "jest-axe";
import { expect } from "vitest";

// Registered once for the whole suite rather than in each spec that runs axe:
// the matcher is global state, so repeating expect.extend per file was ten
// chances for one of them to be forgotten. The rule set itself lives in
// src/test-utils/axe.ts.
expect.extend(toHaveNoViolations);
