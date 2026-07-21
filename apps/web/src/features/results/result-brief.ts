// Pure, deterministic plain-text formatter for the copy/download brief
// actions. Includes only user-facing DesignSpec content — never an id,
// signed URL, source selection, questionnaire answer, provider detail,
// storage metadata or prompt text (none of that is even reachable from the
// curated DesignResult type this function accepts). When present,
// inspiration_acknowledgements contributes only title/attribution and the
// metadata-only limitation note — never a provider cue, alt text, cultural
// context, asset id or URL (also not reachable from this type).

import { DEMO_RESULT_DISCLOSURE } from "./demo-disclosure";
import type { DesignResult } from "@/lib/api";

const GENERIC_DISCLAIMER =
  "This is an AI-assisted visual concept for concept visualisation only. " +
  "It is not a photograph of a finished garment and not a sewing pattern, " +
  "and it does not guarantee that a garment can be constructed exactly as shown.";

export function formatDesignBrief(result: DesignResult): string {
  const lines: string[] = [];

  lines.push(result.title, "", result.concept_summary, "");

  lines.push("Garment breakdown");
  lines.push(`Overall form: ${result.garment_breakdown.overall_form}`);
  lines.push(`Garment components: ${result.garment_breakdown.garment_components.join(", ")}`);
  lines.push(`Silhouette: ${result.garment_breakdown.silhouette}`);
  lines.push(`Drape or layering: ${result.garment_breakdown.drape_or_layering}`);
  lines.push(`Key proportions: ${result.garment_breakdown.key_proportions}`, "");

  lines.push("Colour story");
  lines.push(`Palette: ${result.colour_story.palette_summary}`);
  lines.push(`Placement: ${result.colour_story.placement}`);
  lines.push(`Rationale: ${result.colour_story.rationale}`, "");

  lines.push("Fabrics and texture");
  for (const fabric of result.fabrics_and_texture) {
    lines.push(`- ${fabric.fabric} (${fabric.placement}): ${fabric.finish_and_movement}`);
  }
  lines.push("");

  lines.push("Embellishment plan");
  lines.push(`Techniques: ${result.embellishment_plan.techniques.join(", ")}`);
  lines.push(`Density: ${result.embellishment_plan.density}`);
  lines.push(`Placement: ${result.embellishment_plan.placement.join(", ")}`);
  lines.push(`Motifs: ${result.embellishment_plan.motifs.join(", ")}`);
  lines.push(`Restraint notes: ${result.embellishment_plan.restraint_notes}`, "");

  lines.push("Coverage and drape");
  lines.push(`Sleeves: ${result.coverage_and_drape.sleeves}`);
  lines.push(`Neckline: ${result.coverage_and_drape.neckline}`);
  lines.push(`Back and midriff: ${result.coverage_and_drape.back_and_midriff}`);
  lines.push(`Head covering: ${result.coverage_and_drape.head_covering}`);
  lines.push(`Dupatta or saree drape: ${result.coverage_and_drape.dupatta_or_saree_drape}`, "");

  lines.push("Cultural context");
  if (result.cultural_context.regional_direction) {
    lines.push(`Regional direction: ${result.cultural_context.regional_direction}`);
  }
  lines.push(`Interpretation notes: ${result.cultural_context.interpretation_notes.join(" ")}`);
  lines.push(`Safeguards: ${result.cultural_context.safeguards.join(" ")}`, "");

  lines.push("Styling notes");
  for (const note of result.styling_notes) lines.push(`- ${note}`);
  lines.push("");

  lines.push("Construction caveats");
  for (const caveat of result.construction_caveats) lines.push(`- ${caveat}`);
  lines.push("");

  if (result.inspiration_acknowledgements.length > 0) {
    lines.push("Inspiration acknowledgements");
    for (const acknowledgement of result.inspiration_acknowledgements) {
      lines.push(
        acknowledgement.attribution
          ? `- ${acknowledgement.title} — ${acknowledgement.attribution}`
          : `- ${acknowledgement.title}`,
      );
    }
    lines.push(
      "Selected inspirations influenced this concept through staff-curated descriptions. " +
        "The source images themselves were not sent to the generation models, and the result " +
        "is not an exact reproduction.",
    );
    lines.push("");
  }

  lines.push(GENERIC_DISCLAIMER);
  if (result.is_demo) {
    lines.push("", DEMO_RESULT_DISCLOSURE);
  }

  return lines.join("\n");
}
