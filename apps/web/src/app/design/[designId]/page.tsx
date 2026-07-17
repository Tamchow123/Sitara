"use client";

import { useParams } from "next/navigation";

import { QuestionnaireWizard } from "@/features/questionnaire/QuestionnaireWizard";

// Resume: reconstruct the wizard from the persisted answers and the design's
// linked questionnaire. Ownership is enforced server-side (a foreign design is
// an indistinguishable 404).
export default function DesignPage() {
  const params = useParams<{ designId: string }>();
  const designId = typeof params.designId === "string" ? params.designId : "";
  return <QuestionnaireWizard initialDesignId={designId} />;
}
