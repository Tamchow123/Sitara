"use client";

import { QuestionnaireWizard } from "@/features/questionnaire/QuestionnaireWizard";

// Anonymous design creation is fully supported — no account required. The
// Design is created on the first successful save, not on visiting this page.
export default function NewDesignPage() {
  return <QuestionnaireWizard />;
}
