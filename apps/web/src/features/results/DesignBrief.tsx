"use client";

// Renders the complete DesignSpec-derived result: prominent disclaimers near
// the heading, then every documented section as semantic headings/lists/
// description lists. React's normal escaping stays enabled throughout — no
// dangerouslySetInnerHTML anywhere in this file.

import { useState } from "react";

import { formatDesignBrief } from "./result-brief";
import type { DesignResult } from "@/lib/api";

type Props = { result: DesignResult };

type CopyStatus = "idle" | "success" | "error";

// Plain narrative strings have no stable identity of their own, so an index
// key is the correct choice here — shared once rather than re-justified at
// each of the eight call sites below.
function NarrativeList({ items, className }: { items: string[]; className?: string }) {
  return (
    <ul className={className}>
      {items.map((item, index) => (
        // eslint-disable-next-line react/no-array-index-key -- narrative strings have no stable id
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}

export function DesignBrief({ result }: Props) {
  const [copyStatus, setCopyStatus] = useState<CopyStatus>("idle");

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(formatDesignBrief(result));
      setCopyStatus("success");
    } catch {
      setCopyStatus("error");
    }
  }

  function handleDownloadBrief() {
    const blob = new Blob([formatDesignBrief(result)], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    try {
      const link = document.createElement("a");
      link.href = url;
      link.download = "sitara-design-brief.txt";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  return (
    <div className="design-brief">
      <div className="result-disclaimer" role="note" aria-label="Concept disclaimer">
        <p>
          This is an <strong>AI-assisted visual concept</strong>, not a photograph of a finished
          garment. It is concept visualisation only — not a sewing pattern — and does not
          guarantee that a garment can be constructed exactly as shown. Colours, materials and
          fine details may differ when interpreted physically.
        </p>
      </div>

      <section aria-labelledby="brief-summary">
        <h2 id="brief-summary">Concept summary</h2>
        <p>{result.concept_summary}</p>
      </section>

      <section aria-labelledby="brief-garment">
        <h2 id="brief-garment">Garment breakdown</h2>
        <dl>
          <dt>Overall form</dt>
          <dd>{result.garment_breakdown.overall_form}</dd>
          <dt>Garment components</dt>
          <dd>
            <NarrativeList items={result.garment_breakdown.garment_components} />
          </dd>
          <dt>Silhouette</dt>
          <dd>{result.garment_breakdown.silhouette}</dd>
          <dt>Drape or layering</dt>
          <dd>{result.garment_breakdown.drape_or_layering}</dd>
          <dt>Key proportions</dt>
          <dd>{result.garment_breakdown.key_proportions}</dd>
        </dl>
      </section>

      <section aria-labelledby="brief-colour">
        <h2 id="brief-colour">Colour story</h2>
        <dl>
          <dt>Palette</dt>
          <dd>{result.colour_story.palette_summary}</dd>
          <dt>Placement</dt>
          <dd>{result.colour_story.placement}</dd>
          <dt>Rationale</dt>
          <dd>{result.colour_story.rationale}</dd>
        </dl>
      </section>

      <section aria-labelledby="brief-fabrics">
        <h2 id="brief-fabrics">Fabrics and texture</h2>
        <ul className="fabrics-list">
          {result.fabrics_and_texture.map((fabric, index) => (
            // eslint-disable-next-line react/no-array-index-key -- fabric entries have no stable id
            <li key={index}>
              <strong>{fabric.fabric}</strong> — {fabric.placement}. {fabric.finish_and_movement}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="brief-embellishment">
        <h2 id="brief-embellishment">Embellishment plan</h2>
        <dl>
          <dt>Techniques</dt>
          <dd>
            <NarrativeList items={result.embellishment_plan.techniques} />
          </dd>
          <dt>Density</dt>
          <dd>{result.embellishment_plan.density}</dd>
          <dt>Placement</dt>
          <dd>
            <NarrativeList items={result.embellishment_plan.placement} />
          </dd>
          <dt>Motifs</dt>
          <dd>
            <NarrativeList items={result.embellishment_plan.motifs} />
          </dd>
          <dt>Restraint notes</dt>
          <dd>{result.embellishment_plan.restraint_notes}</dd>
        </dl>
      </section>

      <section aria-labelledby="brief-coverage">
        <h2 id="brief-coverage">Coverage and drape</h2>
        <dl>
          <dt>Sleeves</dt>
          <dd>{result.coverage_and_drape.sleeves}</dd>
          <dt>Neckline</dt>
          <dd>{result.coverage_and_drape.neckline}</dd>
          <dt>Back and midriff</dt>
          <dd>{result.coverage_and_drape.back_and_midriff}</dd>
          <dt>Head covering</dt>
          <dd>{result.coverage_and_drape.head_covering}</dd>
          <dt>Dupatta or saree drape</dt>
          <dd>{result.coverage_and_drape.dupatta_or_saree_drape}</dd>
        </dl>
      </section>

      <section aria-labelledby="brief-cultural">
        <h2 id="brief-cultural">Cultural context</h2>
        {result.cultural_context.regional_direction && (
          <p>
            <strong>Regional direction:</strong> {result.cultural_context.regional_direction}
          </p>
        )}
        <h3>Interpretation notes</h3>
        <NarrativeList items={result.cultural_context.interpretation_notes} />
        <h3>Safeguards</h3>
        <NarrativeList items={result.cultural_context.safeguards} />
      </section>

      <section aria-labelledby="brief-styling">
        <h2 id="brief-styling">Styling notes</h2>
        <NarrativeList items={result.styling_notes} className="styling-list" />
      </section>

      <section aria-labelledby="brief-caveats">
        <h2 id="brief-caveats">Construction caveats</h2>
        <NarrativeList items={result.construction_caveats} />
      </section>

      {result.inspiration_acknowledgements.length > 0 && (
        <section aria-labelledby="brief-inspiration">
          <h2 id="brief-inspiration">Inspiration acknowledgements</h2>
          <p>
            Selected inspirations influenced this concept through staff-curated descriptions. The
            source images themselves were not sent to the generation models, and the result is not
            an exact reproduction.
          </p>
          <ul className="inspiration-acknowledgements">
            {result.inspiration_acknowledgements.map((acknowledgement) => (
              <li key={acknowledgement.position}>
                <strong>{acknowledgement.title}</strong>
                {acknowledgement.attribution ? ` — ${acknowledgement.attribution}` : null}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="result-actions">
        <button type="button" onClick={() => void handleCopy()}>
          Copy brief
        </button>
        <button type="button" onClick={handleDownloadBrief}>
          Download brief
        </button>
        <p role="status" aria-live="polite" className="copy-status">
          {copyStatus === "success" && "Brief copied to clipboard."}
          {copyStatus === "error" && "Could not copy the brief. Please try again."}
        </p>
      </div>
    </div>
  );
}
