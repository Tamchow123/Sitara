"use client";

// The inspiration selection step. Shows the approved public catalogue as
// keyboard-operable toggle cards (aria-pressed, not colour-only), enforces the
// zero-to-three limit on the client (the server remains authoritative), and
// renders any previously-selected asset that is no longer eligible as a
// neutral placeholder the user can remove. Images use a plain <img> (no
// Next.js optimisation/proxy) so the backend's no-store eligibility checks
// always apply. No storage path, hash, rights evidence or internal metadata
// is ever shown.

import type { PublicAsset } from "./types";

type Props = {
  assets: PublicAsset[];
  selection: string[];
  max: number;
  onChange: (ids: string[]) => void;
};

export function InspirationPicker({ assets, selection, max, onChange }: Props) {
  const catalogueIds = new Set(assets.map((asset) => asset.id));
  // Selected ids that are no longer in the eligible catalogue → unavailable.
  const unavailable = selection.filter((id) => !catalogueIds.has(id));
  const selectedCount = selection.length;

  const toggle = (assetId: string): void => {
    if (selection.includes(assetId)) {
      onChange(selection.filter((id) => id !== assetId));
      return;
    }
    if (selectedCount >= max) return; // client block; server also rejects
    onChange([...selection, assetId]);
  };

  const remove = (assetId: string): void => {
    onChange(selection.filter((id) => id !== assetId));
  };

  return (
    <div className="inspiration">
      <p className="field-help" id="inspiration-help">
        Choose up to {max} inspiration images (optional). {selectedCount} of {max} selected.
        Sitara uses each selected image&apos;s staff-written description as a secondary visual
        cue — your questionnaire answers remain authoritative, the image files themselves are not
        sent to the AI models in this version, and the generated concept will not be an exact
        copy.
      </p>

      {unavailable.length > 0 && (
        <ul className="inspiration-unavailable" aria-label="Unavailable selections">
          {unavailable.map((id) => (
            <li key={id} className="inspiration-card inspiration-card-unavailable">
              <p>This inspiration is no longer available.</p>
              <button type="button" onClick={() => remove(id)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {assets.length === 0 && unavailable.length === 0 ? (
        <p className="empty-state">No inspiration images are available yet.</p>
      ) : (
        <ul className="inspiration-grid" aria-describedby="inspiration-help">
          {assets.map((asset) => {
            const selected = selection.includes(asset.id);
            const position = selection.indexOf(asset.id) + 1;
            const blocked = !selected && selectedCount >= max;
            return (
              <li key={asset.id}>
                <button
                  type="button"
                  className={`inspiration-card${selected ? " inspiration-card-selected" : ""}`}
                  aria-pressed={selected}
                  disabled={blocked}
                  onClick={() => toggle(asset.id)}
                >
                  {/* Plain <img>, never next/image: the backend's no-store
                      eligibility checks must apply to every request so a
                      rights-revoked image is never proxied or cached. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    className="inspiration-thumb"
                    src={asset.thumbnail_url}
                    alt={asset.alt_text}
                    loading="lazy"
                    width={512}
                    height={512}
                  />
                  <span className="inspiration-meta">
                    <span className="inspiration-title">{asset.title}</span>
                    {asset.cultural_context ? (
                      <span className="inspiration-context">{asset.cultural_context}</span>
                    ) : null}
                    {asset.attribution ? (
                      <span className="inspiration-attribution">{asset.attribution}</span>
                    ) : null}
                    <span className="inspiration-state">
                      {selected ? `Selected (${position})` : "Not selected"}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
