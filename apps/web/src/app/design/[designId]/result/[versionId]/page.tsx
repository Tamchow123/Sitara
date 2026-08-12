"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { FinishAndHandBack } from "@/features/shopfloor/FinishAndHandBack";
import { DesignResult } from "@/features/results/DesignResult";

export default function DesignResultPage() {
  const params = useParams<{ designId: string; versionId: string }>();
  const designId = typeof params.designId === "string" ? params.designId : "";
  const versionId = typeof params.versionId === "string" ? params.versionId : "";
  return (
    // The concept screen is the handoff's widest layout: a sticky render
    // column beside the specification.
    <AppShell width="wide" actions={<FinishAndHandBack />}>
      <DesignResult designId={designId} versionId={versionId} />
    </AppShell>
  );
}
