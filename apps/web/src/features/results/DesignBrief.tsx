"use client";

// The complete DesignSpec-derived result, laid out as `Sitara Concept.dc.html`
// lays it out: the concept summary in the display face, then the specification
// as collapsible cards, all closed on arrival. React's normal escaping stays
// enabled throughout — no dangerouslySetInnerHTML anywhere in this file.

import { useState } from "react";

import { BriefSection } from "./BriefSection";
import { DEMO_RESULT_DISCLOSURE } from "./demo-disclosure";
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

// A collapsed card shows one line. These are derived from the spec rather than
// written by hand so they can never describe a different concept from the one
// inside the card.
function firstSentence(text: string, fallback: string): string {
  const trimmed = (text ?? "").trim();
  if (!trimmed) return fallback;
  const stop = trimmed.search(/[.!?](\s|$)/);
  const sentence = stop === -1 ? trimmed : trimmed.slice(0, stop);
  return sentence.length > 90 ? `${sentence.slice(0, 87).trimEnd()}…` : sentence;
}

export function DesignBrief({ result }: Props) {
  const [copyStatus, setCopyStatus] = useState<CopyStatus>("idle");
  // Which cards are open, and whether "Expand all" is in force. Both are view
  // state only — nothing here is persisted or sent anywhere.
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [allOpen, setAllOpen] = useState(false);

  const isOpen = (id: string) => allOpen || open[id] === true;
  const toggle = (id: string) =>
    setOpen((current) => {
      const next = { ...current, [id]: !isOpen(id) };
      setAllOpen(false);
      return next;
    });

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(formatDesignBrief(result));
      setCopyStatus("success");
    } catch {
      setCopyStatus("error");
    }
  }

  function handleDownloadBrief() {
    const blob = new Blob([formatDesignBrief(result)], {
      type: "text/plain;charset=utf-8",
    });
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
      <p className="brief-summary">{result.concept_summary}</p>

      <div className="result-disclaimer" role="note" aria-label="Concept disclaimer">
        <p>
          This is an <strong>AI-assisted visual concept</strong>, not a photograph of a finished
          garment. It is concept visualisation only — not a sewing pattern — and does not
          guarantee that a garment can be constructed exactly as shown. Colours, materials and
          fine details may differ when interpreted physically.
        </p>
      </div>

      {result.is_demo && (
        <div className="demo-disclaimer" role="note" aria-label="Demo disclaimer">
          <p>{DEMO_RESULT_DISCLOSURE}</p>
        </div>
      )}

      <div className="brief-toolbar">
        <h2 className="kicker" id="brief-specification">
          Design specification
        </h2>
        <button
          type="button"
          className="btn btn-ghost brief-expand-all"
          onClick={() => {
            setAllOpen((current) => !current);
            setOpen({});
          }}
        >
          {allOpen ? "Collapse all" : "Expand all"}
        </button>
      </div>

      <BriefSection
        id="brief-garment"
        title="Garment breakdown"
        summary={firstSentence(result.garment_breakdown.overall_form, "The shape of the outfit")}
        open={isOpen("brief-garment")}
        onToggle={() => toggle("brief-garment")}
      >
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
      </BriefSection>

      <BriefSection
        id="brief-colour"
        title="Colour story"
        summary={firstSentence(result.colour_story.palette_summary, "The palette")}
        open={isOpen("brief-colour")}
        onToggle={() => toggle("brief-colour")}
      >
        <dl>
          <dt>Palette</dt>
          <dd>{result.colour_story.palette_summary}</dd>
          <dt>Placement</dt>
          <dd>{result.colour_story.placement}</dd>
          <dt>Rationale</dt>
          <dd>{result.colour_story.rationale}</dd>
        </dl>
      </BriefSection>

      <BriefSection
        id="brief-fabrics"
        title="Fabrics and texture"
        summary={
          result.fabrics_and_texture.map((fabric) => fabric.fabric).join(", ") || "The cloth"
        }
        open={isOpen("brief-fabrics")}
        onToggle={() => toggle("brief-fabrics")}
      >
        <ul className="fabrics-list">
          {result.fabrics_and_texture.map((fabric, index) => (
            // eslint-disable-next-line react/no-array-index-key -- fabric entries have no stable id
            <li key={index}>
              <strong>{fabric.fabric}</strong> — {fabric.placement}. {fabric.finish_and_movement}
            </li>
          ))}
        </ul>
      </BriefSection>

      <BriefSection
        id="brief-embellishment"
        title="Embellishment plan"
        summary={firstSentence(result.embellishment_plan.density, "The handwork")}
        open={isOpen("brief-embellishment")}
        onToggle={() => toggle("brief-embellishment")}
      >
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
      </BriefSection>

      <BriefSection
        id="brief-coverage"
        title="Coverage and drape"
        summary={firstSentence(result.coverage_and_drape.sleeves, "Coverage")}
        open={isOpen("brief-coverage")}
        onToggle={() => toggle("brief-coverage")}
      >
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
      </BriefSection>

      <BriefSection
        id="brief-cultural"
        title="Cultural context"
        summary={
          result.cultural_context.regional_direction ||
          firstSentence(
            result.cultural_context.interpretation_notes[0] ?? "",
            "How the traditions were read",
          )
        }
        open={isOpen("brief-cultural")}
        onToggle={() => toggle("brief-cultural")}
      >
        {result.cultural_context.regional_direction && (
          <p>
            <strong>Regional direction:</strong> {result.cultural_context.regional_direction}
          </p>
        )}
        <h3>Interpretation notes</h3>
        <NarrativeList items={result.cultural_context.interpretation_notes} />
        <h3>Safeguards</h3>
        <NarrativeList items={result.cultural_context.safeguards} />
      </BriefSection>

      <BriefSection
        id="brief-styling"
        title="Styling notes"
        summary={firstSentence(result.styling_notes[0] ?? "", "How to wear it")}
        open={isOpen("brief-styling")}
        onToggle={() => toggle("brief-styling")}
      >
        <NarrativeList items={result.styling_notes} className="styling-list" />
      </BriefSection>

      <BriefSection
        id="brief-caveats"
        title="Construction caveats"
        summary="A concept, not a sewing pattern — for a tailor to assess"
        open={isOpen("brief-caveats")}
        onToggle={() => toggle("brief-caveats")}
      >
        <NarrativeList items={result.construction_caveats} />
      </BriefSection>

      {result.inspiration_acknowledgements.length > 0 && (
        <BriefSection
          id="brief-inspiration"
          title="Inspiration acknowledgements"
          summary={result.inspiration_acknowledgements.map((a) => a.title).join(", ")}
          open={isOpen("brief-inspiration")}
          onToggle={() => toggle("brief-inspiration")}
        >
          {/* This paragraph used to say the source images "were not sent to the
              generation models". ADR 0019 reversed that for the references a
              user actually selects, and the repository's own rules forbid
              describing the exposure as removed. So it says what is true of THIS
              concept: in demo mode nothing left the machine, and otherwise the
              chosen references were sent to the image provider. */}
          {result.is_demo ? (
            <p>
              These looks guided the concept through staff-written descriptions of them. This
              concept came from Sitara&apos;s demo pack, so no image — neither these nor anything
              you uploaded — was sent to an AI provider. The result is not a reproduction of any of
              them.
            </p>
          ) : (
            <p>
              These looks guided the concept, both through staff-written descriptions of them and as
              visual references sent to the AI image provider that drew it. The result is not a
              reproduction of any of them.
            </p>
          )}
          <ul className="inspiration-acknowledgements">
            {result.inspiration_acknowledgements.map((acknowledgement) => (
              <li key={acknowledgement.position}>
                <strong>{acknowledgement.title}</strong>
                {acknowledgement.attribution ? ` — ${acknowledgement.attribution}` : null}
              </li>
            ))}
          </ul>
        </BriefSection>
      )}

      <div className="result-actions">
        <button type="button" className="btn btn-secondary" onClick={() => void handleCopy()}>
          Copy brief
        </button>
        <button type="button" className="btn btn-secondary" onClick={handleDownloadBrief}>
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
