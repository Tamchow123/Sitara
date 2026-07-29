"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { GenerationProgress } from "@/features/generation/GenerationProgress";

export default function DesignGenerationPage() {
  const params = useParams<{ designId: string; jobId: string }>();
  const designId = typeof params.designId === "string" ? params.designId : "";
  const jobId = typeof params.jobId === "string" ? params.jobId : "";
  return (
    // Generation runs as a durable background job, so leaving this page does
    // not stop it. Saying so beside Home is the difference between a user
    // waiting on a tab and a user believing they have to.
    <AppShell homeHint="Sitara keeps working if you leave this page — your concept will be waiting.">
      <GenerationProgress designId={designId} jobId={jobId} />
    </AppShell>
  );
}
