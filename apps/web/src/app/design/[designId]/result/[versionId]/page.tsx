"use client";

import { useParams } from "next/navigation";

import { DesignResult } from "@/features/results/DesignResult";

export default function DesignResultPage() {
  const params = useParams<{ designId: string; versionId: string }>();
  const designId = typeof params.designId === "string" ? params.designId : "";
  const versionId = typeof params.versionId === "string" ? params.versionId : "";
  return <DesignResult designId={designId} versionId={versionId} />;
}
