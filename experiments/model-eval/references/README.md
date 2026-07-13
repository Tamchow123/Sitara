# Reference images

Rights-approved inspiration references for the `reference_image` mode.

## Hard rules

- **Every** reference must have an entry in `manifest.yaml` with
  `rights_status: verified` before the runner will send it to a provider.
  Anything else (`pending`, `rejected`, or missing from the manifest) is
  rejected with a recorded skip — no provider call happens for it.
- **Never** scrape designer websites, Pinterest, Instagram or other image
  sources, and never copy bridal photography you do not have rights to.
  Acceptable sources: images you own, images explicitly licensed for this
  use, or appropriately licensed stock with the licence recorded.
- Image **files** go in `references/local/` which is gitignored. A file may
  only be committed to the repository if its licence explicitly permits
  redistribution AND its manifest entry says `may_be_committed: true`.
- Remember the provider-terms caveat recorded in `../TERMS_SNAPSHOT.md`:
  BFL's terms include a licence for the provider to use inputs to improve
  its technology. Do not submit reference images whose rights terms are
  incompatible with that until the unresolved questions there are settled.

## Adding a reference

1. Obtain the image and confirm its usage rights in writing.
2. Save it as `references/local/<id>.jpg` (or .png/.webp).
3. Copy the matching example entry from `manifest.example.yaml` into
   `manifest.yaml`, fill in every field, set `rights_status: verified`,
   `verified_by`, and `verified_on`.
4. `python -m model_eval.cli plan --config configs/finalists.yaml` — briefs
   whose `reference_ids` match your entry ids will now include
   reference_image-mode requests for reference-capable models.

The brief ids that expect references out of the box: `ref-lehenga-red`
(fin-lehenga-pheras-classic), `ref-lehenga-ivory` (fin-lehenga-nikah-ivory),
`ref-gharara-gold` (fin-gharara-baraat-royal), `ref-anarkali-purple`
(fin-anarkali-baraat-regal).

Tests use tiny generated placeholder images only — never real bridal
photography.
