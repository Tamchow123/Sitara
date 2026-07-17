"use client";

import { useParams } from "next/navigation";

import { ReviewSummary } from "@/features/questionnaire/ReviewSummary";

export default function DesignReviewPage() {
  const params = useParams<{ designId: string }>();
  const designId = typeof params.designId === "string" ? params.designId : "";
  return <ReviewSummary designId={designId} />;
}
