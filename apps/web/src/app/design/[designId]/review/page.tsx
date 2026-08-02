"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { ReviewSummary } from "@/features/questionnaire/ReviewSummary";

export default function DesignReviewPage() {
  const params = useParams<{ designId: string }>();
  const designId = typeof params.designId === "string" ? params.designId : "";
  return (
    <AppShell homeHint="Your answers are saved — leaving this page keeps them.">
      <ReviewSummary designId={designId} />
    </AppShell>
  );
}
