"use client";

import { useParams } from "next/navigation";

import { GenerationProgress } from "@/features/generation/GenerationProgress";

export default function DesignGenerationPage() {
  const params = useParams<{ designId: string; jobId: string }>();
  const designId = typeof params.designId === "string" ? params.designId : "";
  const jobId = typeof params.jobId === "string" ? params.jobId : "";
  return <GenerationProgress designId={designId} jobId={jobId} />;
}
