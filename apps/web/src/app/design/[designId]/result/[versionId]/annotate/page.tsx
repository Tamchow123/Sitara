"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/AppShell";
import { AnnotationWorkspace } from "@/features/annotations/AnnotationWorkspace";

// Private and ownership-backed by the API. The Next.js middleware is a
// navigation optimisation only — a foreign or nonexistent design is an
// indistinguishable 404 from Django, which is the actual boundary.
export default function AnnotatePage() {
  const params = useParams<{ designId: string; versionId: string }>();
  const designId = typeof params.designId === "string" ? params.designId : "";
  const versionId = typeof params.versionId === "string" ? params.versionId : "";
  return (
    // The widest layout in the system: a tool rail, a canvas and a list panel
    // side by side.
    <AppShell width="wide">
      <AnnotationWorkspace designId={designId} versionId={versionId} />
    </AppShell>
  );
}
