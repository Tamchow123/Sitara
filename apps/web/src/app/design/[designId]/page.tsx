"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { QuestionnaireWizard } from "@/features/questionnaire/QuestionnaireWizard";

// Resume: reconstruct the wizard from the persisted answers and the design's
// linked questionnaire. Ownership is enforced server-side (a foreign design is
// an indistinguishable 404).
export default function DesignPage() {
  const params = useParams<{ designId: string }>();
  const designId = typeof params.designId === "string" ? params.designId : "";
  return (
    // The Home hint matters here: the draft is saved on the server as the user
    // answers, so going Home is not "throw this away". "Start over" is the
    // separate, confirmed, destructive action.
    <AppShell homeHint="Your answers are saved as you go — leaving this page keeps them.">
      <QuestionnaireWizard initialDesignId={designId} />
    </AppShell>
  );
}
