"use client";

import { AppShell } from "@/components/AppShell";
import { QuestionnaireWizard } from "@/features/questionnaire/QuestionnaireWizard";
import { FinishAndHandBack } from "@/features/shopfloor/FinishAndHandBack";

// Anonymous design creation is fully supported — no account required. The
// Design is created on the first successful save, not on visiting this page.
export default function NewDesignPage() {
  return (
    <AppShell homeHint="Your answers are saved as you go." actions={<FinishAndHandBack />}>
      <QuestionnaireWizard />
    </AppShell>
  );
}
