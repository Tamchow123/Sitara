# Sitara Phase 17 — High-fidelity UI completion and accessibility

Known repository baseline when this specification was written:

```text
ec4d82d86262072b25e2c6c8733fd1091101de73
```

The latest `main` must be a clean descendant of that commit and must contain:

- delivered Phases 1–16;
- the generated-image composition and coverage-first prompt work;
- the complete Phase 16B questionnaire v4, DesignSpec v3, upload and reference-image work;
- the approved Claude design handoff under `design_handoff_sitara_flow/`;
- the current private questionnaire, generation, result and one-refinement journeys.

Phase 17 is a frontend fidelity, responsive-design and accessibility phase. It must not redesign Sitara from memory, substitute a generic bridal theme, or treat the current functional interface as the visual reference.

Before changing anything:

1. Run `git status --short`, `git log -20 --oneline`, `git rev-parse HEAD`, and `git branch --show-current`.
2. Confirm the working tree is clean and Phase 16B is merged.
3. Confirm `design_handoff_sitara_flow/` exists at the repository root and contains all six `.dc.html` prototypes, its README, Organic design-system assets and referenced images.
4. Serve the handoff over HTTP and open every prototype before writing application CSS or components. Do not inspect only screenshots or the README summary.
5. Report missing handoff files, broken image paths, unapproved assets, current-route conflicts or later-phase application code before proceeding.
6. Do not work directly on `main`; follow the repository’s `/run-phase`, branch, per-commit council-review, push and draft-PR workflow.
7. Use the current `apps/web` application, typed API client, TanStack Query lifecycle, questionnaire state, ownership rules and generation routes. Do not create a second frontend, prototype-only route tree or alternative API client.

## Main objective

Turn Sitara’s fully functional journey into a cohesive, high-fidelity implementation of the approved Claude design handoff while completing the roadmap accessibility pass.

The delivered experience must visibly and behaviourally align with the prototypes for:

- the landing page;
- the full questionnaire flow;
- the three-part colours page;
- option-card imagery and framing for every visual category;
- a consistent Home action that returns to the start;
- generation and refinement progress, including animation tied to real job states;
- concept/result presentation;
- the constrained refinement experience;
- version history/comparison;
- loading, empty, unavailable and controlled error states;
- privacy and concept-visualisation information;
- mobile, tablet and desktop layouts;
- keyboard, screen-reader, zoom, contrast and reduced-motion use.

The current implementation is known not to match the handoff closely enough. Phase 17 must explicitly correct:

- the landing page layout, branding, typography, imagery and hierarchy;
- the colours page layout and interaction styling;
- category option images that do not match the images selected in the handoff;
- inconsistent or missing navigation back to the Sitara start page;
- the generation pages, including narrated phases and animation;
- any remaining questionnaire, result, refinement or history styling that is functionally complete but not visually faithful.

This is not permission to change questionnaire semantics, generation contracts or product limits. Visual fidelity must sit on top of the existing secure application.

## Safety mode throughout this phase

No AI provider work is required.

During implementation, automated tests, council review and all manual checkpoints keep:

```text
DEMO_MODE=true
ALLOW_PAID_AI_CALLS=false
LIVE_GENERATION_ENABLED=false
```

Use no real Anthropic or Replicate credentials and make no paid calls.

Never run:

```text
docker compose down --volumes
```

Do not delete or reset development volumes.

Do not place real user questionnaire answers, uploaded inspiration images, private generated concepts, signed URLs, provider responses, credentials or rights evidence into screenshots, fixtures, visual-regression baselines, logs or commits. Use only repository-approved handoff assets and synthetic/demo data.

# Design source of truth — binding requirement

The visual and interaction source of truth for this phase is:

```text
design_handoff_sitara_flow/
```

Claude Code must read and verify the current versions of:

```text
design_handoff_sitara_flow/README.md
design_handoff_sitara_flow/Sitara Home.dc.html
design_handoff_sitara_flow/Sitara Questionnaire.dc.html
design_handoff_sitara_flow/Sitara Generation.dc.html
design_handoff_sitara_flow/Sitara Concept.dc.html
design_handoff_sitara_flow/Sitara Amendments.dc.html
design_handoff_sitara_flow/Sitara History.dc.html
design_handoff_sitara_flow/_ds/organic-ccb06f12-17fe-45b2-9248-ebf010085646/styles.css
```

It must also inspect the handoff’s `uploads/images/` hierarchy and the actual image references used by each `.dc.html` file.

Requirements:

- Serve the `.dc.html` files over HTTP so their image paths and prototype runtime resolve correctly.
- Compare the running Next.js application side-by-side with the prototypes at matching viewport sizes.
- Treat `styles.css` as the token source of truth; do not approximate its palette, spacing, radii or shadows by eye.
- Recreate the designs using Sitara’s production React/Next.js components and application state.
- Do not copy or ship the prototype’s `support.js`, in-house runtime, inline demo state or timer-based fake generation logic.
- Do not import the handoff HTML directly into production routes.
- Do not use screenshots of the prototype as the application UI.
- Do not silently replace a handoff image with a vaguely similar image.
- If a referenced image is missing, incorrectly licensed, culturally inaccurate or unsuitable for its canonical option, render a deliberate text-only card and report the asset gap rather than displaying the wrong image.

## Precedence where the handoff and application differ

Use this precedence order:

1. Security, privacy, ownership, provider gating, rights controls and immutable historical-data rules in `CLAUDE.md`, delivered ADRs and backend contracts.
2. Existing product constraints and canonical application semantics, including one constrained refinement and the current questionnaire/API schema.
3. The handoff’s visual design, layout, wording, motion principles and interaction presentation.
4. Existing frontend styling that predates the handoff.

Known intentional divergence:

- `Sitara Amendments.dc.html` describes up to three regenerations and a broader free-form amendment workflow.
- Sitara’s delivered product permits exactly one constrained refinement, enforced server-side and represented by the existing refinement categories plus bounded note.
- Phase 17 must visually adapt the amendments/refinement prototype to the existing one-refinement contract. It must not increase the refinement limit, loosen the allowlist, add multi-round refinement or imply that unlimited changes are available.

Record every intentional divergence in the completion report. Do not resolve a conflict by quietly changing product behaviour.

# Read first

Read the current versions of:

- `CLAUDE.md`;
- `.claude/phase-council.json`;
- `.claude/review/README.md`;
- `README.md`;
- `.env.example`;
- `compose.yaml`;
- `.github/workflows/ci.yml`;
- `docs/PROPOSAL.md`;
- `docs/phases/PHASES.md`;
- `docs/phases/phases-7.md`;
- `docs/phases/phases-8.md`;
- `docs/phases/phases-10.md`;
- `docs/phases/phases-12.md`;
- `docs/phases/phases-13.md`;
- `docs/phases/phases-14.md`;
- `docs/phases/phases-15.md`;
- `docs/phases/phases-16.md`;
- `docs/phases/phases-16b.md`;
- ADRs 0003, 0004, 0005, 0006, 0012, 0013, 0015, 0016, 0017, 0018 and 0019;
- every file listed in the binding handoff section;
- the handoff’s image shot list and all asset directories;
- `apps/web/src/app/layout.tsx`;
- `apps/web/src/app/page.tsx` and its tests;
- current global CSS and all route-specific style files;
- shared button, link, form, dialog, drawer, disclosure, toast, image and loading primitives;
- authentication/account navigation components;
- the complete questionnaire route, wizard, progress navigation, visual manifest, colour selector, upload picker, review and draft-persistence components;
- `apps/web/src/features/generation/GenerationProgress.tsx`;
- generation/refinement progress-copy and error modules;
- result, brief, signed-image, refinement and version-comparison components;
- public config and demo banner components;
- generated OpenAPI types and current API client;
- existing unit, axe, Playwright and E2E infrastructure.

# Mandatory pre-implementation audit

Before editing application code, create a concise audit in the phase work log:

| Handoff reference | Production route/component | Current mismatch | Required correction | Asset source | Accessibility concern |
| --- | --- | --- | --- | --- | --- |
| `Sitara Home.dc.html` | landing route | | | | |
| `Sitara Questionnaire.dc.html` | questionnaire/wizard | | | | |
| colour screen | colour selector | | | | |
| image-led categories | visual manifest/cards | | | | |
| `Sitara Generation.dc.html` | generation progress | | | | |
| `Sitara Concept.dc.html` | result route | | | | |
| `Sitara Amendments.dc.html` | refinement panel/route | | | | |
| `Sitara History.dc.html` | comparison/history UI | | | | |
| Organic tokens | global styles/components | | | | |

Before implementation, report:

- the exact current route for each handoff screen;
- which production screens do not have a one-to-one route and how the design will be adapted without duplicate workflows;
- the current shared-shell strategy;
- the exact handoff tokens and font roles to introduce;
- how fonts will be bundled or loaded without a runtime third-party request;
- the current image-manifest keys for every visual question;
- every option whose displayed image differs from the image used by the handoff;
- any handoff asset that is absent, duplicated, incorrectly framed or not approved for production use;
- the current questionnaire colour answer contract and why the restyle does not require an API/schema change;
- the current generation statuses and how animation will reflect real statuses without inventing a percentage or completion time;
- the current signed-image and result-query boundaries that must remain intact;
- the exact refinement constraint that overrides the amendments prototype;
- the current accessibility test coverage and highest-risk gaps.

# Required commit boundaries

Implement as six independently reviewed commits:

1. `feat(frontend): adopt Sitara handoff tokens and branded application shell`
2. `feat(frontend): match landing and questionnaire handoff designs`
3. `feat(frontend): match generation, result, refinement, and history designs`
4. `feat(frontend): polish legal, loading, empty, and error experiences`
5. `test(frontend): add accessibility and visual-fidelity coverage`
6. `docs(phase-17): record fidelity decisions and accessibility evidence`

Do not combine these commits. Each must pass focused tests and the per-commit council before moving on.

# Part A — Organic design system and application shell

## 1. Production design tokens

Create or update a focused token layer based on:

```text
design_handoff_sitara_flow/_ds/organic-ccb06f12-17fe-45b2-9248-ebf010085646/styles.css
```

Requirements:

- Preserve semantic roles for background, surface, text, primary accent, secondary accent, neutral ramps, spacing, radius and elevation.
- Use CSS custom properties as the production source of truth.
- Do not scatter copied hex values and spacing literals across route files.
- Verify text/background combinations meet WCAG 2.1 AA. Where a prototype token fails for a specific text use, choose the closest compliant ramp step and document the divergence.
- Preserve the warm bridal aesthetic rather than the previous generic white/grey styling.
- Keep print/download brief styling readable and independent from decorative effects.
- Do not add a new dark-mode design.

## 2. Typography

Implement the handoff’s typography roles after checking computed prototype styles:

- Cormorant Garamond for the Sitara wordmark and designated display text;
- the Organic heading face for heading roles;
- the Organic body face for body and UI text;
- legible fallback stacks.

Requirements:

- Do not use runtime CSS `@import` for Google Fonts.
- Use Next.js font loading or source-controlled licensed files.
- Load only required weights/subsets.
- Avoid layout shift.
- Maintain text zoom and reflow without clipping.

## 3. Shared brand shell and Home navigation

Create a reusable brand lockup and shell with:

- the handoff star mark and Sitara wordmark;
- a link to `/` from the complete brand lockup;
- a clearly labelled Home action throughout questionnaire, generation, result, refinement, history, authentication and account journeys;
- a skip-to-main-content link;
- compact account/sign-in navigation;
- visible keyboard focus;
- responsive layout.

Home requirements:

- It must have an accessible name containing “Home”; do not rely on an unexplained icon.
- Home returns to the landing/start page.
- Leaving the questionnaire must not silently destroy the saved draft.
- “Start over” remains a separate destructive action with confirmation.
- Leaving generation must not imply cancellation of the durable job.
- Do not expose private identifiers in visible navigation labels.

## 4. Shared primitives

Restyle existing production primitives instead of duplicating route-specific variants:

- primary, secondary, ghost and icon buttons;
- links;
- cards and panels;
- native input wrappers;
- radio/checkbox option cards;
- swatches and removable chips;
- dialogs/drawers;
- disclosures;
- banners/notices;
- loading skeletons;
- alerts and toasts.

Include hover, active, focus-visible, disabled and loading states.

# Part B — Landing page fidelity

## 5. Rebuild from `Sitara Home.dc.html`

The landing route must match the handoff rather than the current development-status page.

Required structure:

- centred container using handoff width and spacing;
- branded wordmark;
- two-column hero on suitable widths;
- headline, supporting copy and primary “Start your design” CTA;
- staggered two-image composition using the exact approved handoff hero images;
- graceful single-column mobile layout;
- concise privacy/concept copy and footer links.

Requirements:

- Verify the exact hero image references in the prototype.
- Use the handoff aspect ratio, crop, washed treatment and radii.
- Make an intentional alt-text decision for each image.
- Preserve account access but integrate it into the design.
- Preserve honest demo and concept disclosures without creating a diagnostics dashboard.
- Remove database, Redis, storage and backend readiness details from the customer-facing landing page.
- Render a polished bounded notice if public config cannot be loaded.

Verify at 320×568, 390×844, 768×1024, 1024×768 and 1440×1000.

# Part C — Questionnaire fidelity and correct images

## 6. Match the 16-screen, 9-category structure

Use `Sitara Questionnaire.dc.html` as the presentation reference while retaining questionnaire v4/API-driven content.

Required presentation:

- branded header and reassurance copy;
- desktop category-pill navigation;
- mobile “Step N of 9” bar and accessible “All steps” disclosure;
- category/question kicker;
- one primary question screen at a time;
- fieldset/legend semantics;
- helper and limit text;
- responsive visual-card grids;
- Back, Skip and Continue actions;
- review rows with Edit controls;
- “Left to Sitara’s imagination” for absent optional answers;
- “Begin designing” and confirmed “Start over”.

Preserve required/optional rules, authoritative compatibility validation, no-preference semantics, draft persistence, dependent-answer clearing, upload rights/limits, typed APIs and version immutability.

## 7. Correct every category image mapping

The application currently uses images that do not always match the handoff. Fix this explicitly.

Maintain one source-controlled visual manifest keyed by canonical question and option values. For every option, inspect the actual image used by the prototype.

Audit:

- ceremonies;
- garments;
- cultural direction;
- garment-dependent silhouettes;
- fabrics;
- embroidery;
- richness;
- necklines;
- sleeves;
- back coverage;
- midriff coverage;
- head covering;
- saree drapes;
- dupatta styles;
- curated inspiration presets.

Requirements:

- Use only rights-approved source-controlled visuals.
- Never use private uploads as option imagery.
- Do not silently reuse inspiration assets for taxonomy illustrations without explicit approval.
- Do not send visual paths, alt text or image bytes to providers as canonical answers.
- Do not infer the option solely from a filename; inspect the visible image.
- Preserve cultural distinctions, including gharara/sharara, saree drapes, Anand Karaj, regional styling and head covering.
- Add manifest-integrity and corrected-mapping tests.
- Unknown/missing keys render a complete text card, never another option’s image.

## 8. Match image framing

Use handoff ratios:

- ceremony: 3:2;
- garment, dupatta, inspiration: 3:4;
- culture, silhouette, saree drape: 2:3;
- fabrics: 3:2;
- embroidery and richness: square;
- neckline: 5:4;
- sleeves, back, midriff, head: portrait coverage framing.

Use deliberate `object-position`; never crop away the feature being explained.

## 9. Option cards and information drawer

Requirements:

- Keep native radio/checkbox semantics.
- Make the card label selectable without invalid nested interactions.
- Implement the info action with valid DOM structure.
- Use an accessible bottom sheet/dialog with focus trap, Escape close and focus restoration.
- Respect reduced motion.
- Communicate selection using more than colour.
- Announce selection limits politely only when relevant.

## 10. Rebuild the colours page

Treat the colours page as a special designed screen, not a generic list.

Present v4 answers as:

1. fabric colour;
2. embroidery colour;
3. dupatta colour.

Required design/behaviour:

- compact wrapping circular swatches;
- grouped source-controlled palette;
- selected rings and ticks;
- order badges where supported;
- visible boundaries on light swatches;
- removable selected-colour chips;
- “Match the fabric” for dupatta where supported;
- “Any colour” custom-colour disclosure using the existing bounded hex contract;
- native colour input, visible hex, Add and Cancel;
- no excessive long scrolling;
- no backend/schema change.

Requirements:

- Verify palette and labels against questionnaire v4 and the handoff.
- Keep maximums enforced visually and authoritatively.
- Give every swatch an accessible name.
- Support keyboard selection/removal.
- Normalise and safely display custom colours.
- Provide field-local errors.
- Reflow at 200% zoom without horizontal page scrolling.

## 11. Questionnaire motion and focus

Use restrained handoff motion:

- approximately 18px upward entry plus fade;
- short drawer/picker fade/slide;
- no sequence delaying interaction.

On screen change, focus the new question heading or deliberate landmark. With reduced motion, remove transforms, smooth scrolling and non-essential transitions.

# Part D — Generation progress fidelity

## 12. Match `Sitara Generation.dc.html` using real states

Retain three narrated stages:

```text
Preparing
Design brief
Visual concept
```

Map to:

```text
queued
running_text
running_image
```

Requirements:

- Match the handoff hierarchy and progress treatment.
- Add tasteful state-driven indeterminate animation for the active stage.
- Never auto-advance on a timer.
- Never invent percentage, ETA or completion claims.
- Distinguish completed, active and pending stages.
- Announce meaningful stage changes through one polite live region.
- Do not announce poll frames or animation.
- Preserve polling backoff, focus refetch, terminal handling and redirect.
- Preserve demo/live/refinement wording and all controlled errors.
- Keep the privacy note integrated.
- Provide a clear static state under reduced motion.

Restyle all branches: initial fetch, transient failure, not found, controlled errors, succeeded-without-version, success transition and refinement failure.

# Part E — Result, refinement and history

## 13. Match `Sitara Concept.dc.html`

Adapt the private result route to:

- sticky 3:4 image on desktop;
- specification/brief on the right;
- collapsible specification cards;
- clear mode/version labelling;
- Edit answers and Refine actions;
- integrated copy/download actions;
- visible concept/constructibility disclaimer;
- inspiration acknowledgements and demo disclosure when applicable.

Preserve:

- independent result and signed-image queries;
- readable brief when the image fails;
- memory-only signed URLs and refresh lifecycle;
- no signed URL/storage key/hash logging;
- historical DesignSpec readability;
- private typed downloads.

On mobile, use one column and remove stickiness.

## 14. Adapt `Sitara Amendments.dc.html` to one refinement

Use the handoff composition and accessible lock state, but retain:

- one allowlisted category;
- one bounded note;
- mandatory drift acknowledgement;
- exactly one refinement;
- no image-editing claim;
- honest fresh-generation drift warning;
- no second refinement.

Do not present “3 regenerations” or unlimited free-form changes. The server remains authoritative. Preserve idempotency and original-concept navigation.

## 15. Match `Sitara History.dc.html`

Apply the hierarchy to the existing private version experience:

- latest design prominent;
- original/refined cards clearly labelled;
- version number and generation mode visible;
- responsive grid;
- clear version navigation;
- no public gallery.

Handle original-only, original-plus-one-refinement, image unavailable with brief present, and per-version demo/live provenance.

# Part F — Privacy, disclaimers and non-happy paths

## 16. Privacy and concept-information pages

Add or complete concise pages for:

- privacy information;
- concept visualisation and limitations.

State accurately that designs are private by default, explain anonymous/account ownership at a high level, explain uploaded references are not public, and state that concepts are not sewing patterns or guaranteed constructible.

Do not invent retention periods, provider guarantees or legal conclusions.

## 17. Loading, empty and error states

Audit all Phase 17 routes for:

- loading;
- empty data;
- missing design/version;
- expired/unavailable signed image;
- retryable failure;
- questionnaire unavailable;
- demo pack unavailable;
- generation disabled;
- generation limit/budget reached;
- upload validation failure;
- refinement unavailable/used;
- authentication/account state changes.

Each state must use Organic styling, plain language, one clear next action where possible, safe privacy handling, accessible semantics and stable layout.

# Part G — Accessibility

## 18. WCAG target

Meet WCAG 2.1 AA across landing, questionnaire, generation, result, refinement, history, authentication/account and information pages. Apply practical WCAG 2.2 improvements for focus and target sizing.

## 19. Keyboard and focus

Requirements:

- all actions keyboard operable;
- logical focus order;
- no positive `tabIndex`;
- working skip link;
- deliberate wizard focus movement;
- correct current/completed/locked progress semantics;
- proper dialog trapping/Escape/restoration;
- accessible disclosures;
- error-summary/first-invalid focus;
- sticky UI does not obscure focus;
- Home and Start over remain distinct.

## 20. Screen-reader semantics

Requirements:

- one primary `h1`;
- logical headings;
- fieldset/legend for questions;
- programmatic required/optional state;
- accessible progress states;
- meaningful generation announcements only;
- bounded polite limit messages;
- named swatches;
- intentional alt text;
- named icon controls;
- safe upload preview text;
- expanded-state disclosures;
- no private identifier as user-facing text.

## 21. Contrast and non-colour cues

- 4.5:1 normal text;
- 3:1 large text;
- 3:1 focus/component boundaries;
- selection not shown by colour alone;
- understandable disabled states;
- text/icon/shape cues for status;
- visible light-swatch borders.

## 22. Motion, reflow and touch

- Honour `prefers-reduced-motion`.
- No flashing.
- Progress animation is non-essential and subtle.
- No hover layout shift.
- Verify 320px width and 200% zoom.
- Use approximately 44×44 targets where practical.
- Support orientation changes and long labels.
- Handle mobile safe-area insets.
- Do not rely on hover-only content.

# Part H — Tests

## 23. Unit/component tests

Add focused tests for:

- brand/Home navigation;
- landing CTA/unavailable state;
- every corrected image mapping;
- unknown visual fallback;
- card radio/checkbox semantics;
- info drawer focus/Escape/restoration;
- desktop/mobile progress navigation;
- colours selection, limits, order, custom colour, Match fabric and removal;
- Start over versus Home;
- generation status mapping;
- no timer-driven generation advancement;
- reduced-motion states;
- result disclosure keyboard behaviour;
- one-use refinement lock/copy;
- original-only and original-plus-refinement history;
- alt-text decisions;
- controlled error/retry focus.

Do not use timers to fake backend stage progression.

## 24. Automated accessibility

Run axe against representative states for:

- landing;
- ceremony question;
- visual multi-select;
- colours with picker open;
- info drawer;
- review;
- each generation stage;
- generation failure;
- result with image;
- result with image unavailable but brief present;
- refinement available/locked;
- history;
- privacy/concept pages.

## 25. Playwright/E2E

Using demo mode and synthetic/approved assets:

1. Landing → Start → required questions → Home → return to saved draft.
2. Keyboard-only wizard with Skip, Back, category navigation, colours and Edit.
3. Keyboard info drawer.
4. Mobile custom-colour selection/removal.
5. Inspiration selection and synthetic upload preview/removal.
6. Real controlled queued → running_text → running_image → result rendering.
7. Refresh and resume the same job.
8. Result image failure with brief retained.
9. One refinement, return to original during progress, lock afterwards.
10. Original/refined history.

Prove demo mode constructs no provider client and makes no provider request.

## 26. Visual regression

Add deterministic mobile and desktop screenshots for:

- landing;
- ceremony;
- garment;
- colours;
- fabric/embroidery;
- coverage;
- review;
- generation;
- result;
- refinement;
- history.

Wait for local images/fonts, disable nondeterministic motion during capture, use fixed demo content and never baseline signed URLs, emails, random ids or infrastructure status.

## 27. Standard checks

Run current equivalents of:

```text
docker compose exec web npm test -- --run
docker compose exec web npm run lint
docker compose exec web npm run typecheck
docker compose exec web npm run build
docker compose exec api pytest
```

Run Playwright/E2E and OpenAPI determinism checks if generated types are touched. Keep the repo green at every commit boundary.

# Part I — Manual checkpoint

## 28. Side-by-side verification

Serve `design_handoff_sitara_flow/` over HTTP and compare every prototype with production at matching viewports.

Verify:

- palette;
- typography;
- spacing/max widths;
- radii/borders/shadows;
- landing hero and exact images;
- wordmark/star;
- Home navigation;
- questionnaire progress;
- card columns/ratios;
- exact option images;
- card states;
- info drawer;
- colours page;
- review rows;
- three-stage generation and animation;
- result layout/disclosures;
- one-refinement adaptation;
- history hierarchy;
- responsive reflow;
- loading/errors.

Create a parity matrix using:

- `MATCHED`;
- `INTENTIONAL_DIVERGENCE`;
- `BLOCKED_ASSET_GAP`;
- `FOLLOW_UP` only for genuine out-of-scope work.

Phase 17 cannot complete while landing, colours, category-image, Home-navigation or generation-animation mismatches remain.

## 29. Keyboard-only checkpoint

Complete:

```text
landing
→ questionnaire
→ colours
→ inspiration/upload
→ review
→ generation
→ result
→ refinement
→ version comparison
→ Home
```

## 30. Screen-reader checkpoint

Use NVDA or equivalent to spot-check landing, questionnaire progress/card group, colours, drawer, generation stage update, result image/brief separation and refinement lock. Record browser/screen-reader pair.

## 31. Lighthouse and axe

Audit landing, visual-card question, colours, generation and result. Target Lighthouse accessibility ≥90 and no serious/critical axe violations.

## 32. Reduced motion and zoom

Verify reduced-motion emulation, 200% desktop zoom, 320px width, no clipped sticky UI and no obscured focus.

# Non-goals

Do not add:

- backend/API changes solely for styling;
- a new questionnaire or DesignSpec version;
- new canonical answers;
- prompt/model/provider changes;
- paid generation;
- more than one refinement;
- unrestricted multi-round amendments;
- stylist annotations;
- height/body representation;
- sharing/public gallery/collaboration;
- a new dark-theme design;
- marketing analytics or cookie-consent tooling;
- another frontend framework/component library;
- prototype runtime in production;
- arbitrary remote image URLs;
- public operational-status details on the landing page.

# Acceptance criteria

Phase 17 is complete only when:

1. `design_handoff_sitara_flow/` was served, opened and used for route-by-route verification.
2. Landing matches the handoff layout, typography, colour, imagery and responsive composition.
3. Internal database/Redis/storage readiness is removed from the customer landing page.
4. Every visual option uses the correct approved handoff image or a reported text-only fallback.
5. Colours match the compact three-section handoff while preserving v4 answers.
6. A labelled Home action works throughout without clearing draft or implying job cancellation.
7. Questionnaire matches the 16-screen/9-category presentation and remains schema-driven.
8. Generation matches the three narrated phases with real-status-driven accessible animation.
9. Result matches the concept layout while preserving independent result/image queries.
10. Refinement matches the visual direction but enforces one constrained refinement.
11. Version history matches the original/refined hierarchy.
12. Loading, empty and error states are styled and accessible.
13. Privacy and concept information is accurate and linked.
14. Representative routes have no serious/critical axe violations.
15. Keyboard and screen-reader checkpoints are recorded.
16. Reduced motion, 320px and 200% zoom pass.
17. Lighthouse accessibility is at least 90 on required routes unless a documented false positive has stronger manual evidence.
18. Visual regression covers required deterministic states.
19. Backend, frontend, E2E, lint, typecheck and build checks are green.
20. Demo mode still makes no paid/provider call.
21. No private/signed/user content enters logs, fixtures, screenshots or commits.

# Completion report

Include:

- starting baseline and final HEAD;
- commit list;
- changed files grouped by area;
- completed parity matrix;
- exact corrected option-image mappings;
- blocked asset gaps;
- intentional divergences, especially one refinement;
- viewports/devices checked;
- keyboard journey result;
- screen-reader/browser pair and findings;
- Lighthouse/axe results;
- reduced-motion/zoom results;
- all command results;
- visual-regression baseline list;
- confirmation of no paid credentials/calls;
- confirmation no private user content or signed URL entered logs, screenshots, fixtures or commits;
- council findings and resolutions.

Do not mark complete with “UI polished” or “looks close”. Provide evidence against the handoff files and acceptance criteria.
