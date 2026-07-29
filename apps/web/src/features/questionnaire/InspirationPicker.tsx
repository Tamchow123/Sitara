"use client";

// The inspiration selection step. Shows the approved public catalogue as
// keyboard-operable toggle cards (aria-pressed, not colour-only), enforces the
// zero-to-three limit on the client (the server remains authoritative), and
// renders any previously-selected asset that is no longer eligible as a
// neutral placeholder the user can remove. The cap is on REFERENCES, so
// curated presets and the user's own uploads draw from one shared budget. Images use a plain <img> (no
// Next.js optimisation/proxy) so the backend's no-store eligibility checks
// always apply. No storage path, hash, rights evidence or internal metadata
// is ever shown.

import type { InspirationUpload as Upload } from "@/lib/api";

import { InspirationUpload } from "./InspirationUpload";
import type { PublicAsset } from "./types";

type Props = {
  assets: PublicAsset[];
  selection: string[];
  max: number;
  onChange: (ids: string[]) => void;
  // Uploads share the SAME budget as presets — the cap is on references, not on
  // where they came from. Omitted while no design exists yet to attach them to.
  designId?: string;
  uploads?: Upload[];
  onUploadsChange?: (uploads: Upload[]) => void;
};

export function InspirationPicker({
  assets,
  selection,
  max,
  onChange,
  designId,
  uploads,
  onUploadsChange,
}: Props) {
  const catalogueIds = new Set(assets.map((asset) => asset.id));
  // Selected ids that are no longer in the eligible catalogue → unavailable.
  const unavailable = selection.filter((id) => !catalogueIds.has(id));
  const uploadCount = uploads?.length ?? 0;
  // One budget across both kinds. The server enforces the same total under a
  // row lock; this only keeps the UI from offering what it would reject.
  const usedCount = selection.length + uploadCount;

  const toggle = (assetId: string): void => {
    if (selection.includes(assetId)) {
      onChange(selection.filter((id) => id !== assetId));
      return;
    }
    if (usedCount >= max) return; // client block; server also rejects
    onChange([...selection, assetId]);
  };

  const remove = (assetId: string): void => {
    onChange(selection.filter((id) => id !== assetId));
  };

  return (
    <div className="inspiration">
      <p className="field-help" id="inspiration-help">
        Choose up to {max} inspiration images in total (optional) — from this
        collection, your own photographs, or a mix. {usedCount} of {max} used.
        Your questionnaire answers stay authoritative, and the generated concept
        will not be an exact copy. The images you choose are sent to the external
        AI image provider that draws your concept, along with each catalogue
        image&apos;s staff-written description.
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
            const blocked = !selected && usedCount >= max;
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

      {designId && uploads && onUploadsChange ? (
        <InspirationUpload
          designId={designId}
          uploads={uploads}
          slotsRemaining={Math.max(max - usedCount, 0)}
          onChange={onUploadsChange}
        />
      ) : null}
    </div>
  );
}
