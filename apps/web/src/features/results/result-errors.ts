// Friendly, source-controlled classification for the result query's failure
// modes. Never surfaces a raw backend exception message.

import type { DesignResultFailure } from "@/lib/api";

export class DesignResultQueryError extends Error {
  status: number;
  code: string;

  constructor(failure: DesignResultFailure) {
    super(failure.message);
    this.name = "DesignResultQueryError";
    this.status = failure.status;
    this.code = failure.code;
  }
}

export type ResultErrorKind =
  | "not_found"
  | "not_ready"
  | "service_unavailable"
  | "malformed"
  | "unavailable";

export function classifyResultError(error: unknown): ResultErrorKind {
  if (!(error instanceof DesignResultQueryError)) return "unavailable";
  switch (error.code) {
    case "not_found":
      return "not_found";
    case "design_result_not_ready":
      return "not_ready";
    case "design_result_unavailable":
      return "service_unavailable";
    case "invalid_response":
      return "malformed";
    default:
      return "unavailable";
  }
}

export function resultErrorCopy(kind: ResultErrorKind): { heading: string; message: string } {
  switch (kind) {
    case "not_found":
      return {
        heading: "Result not found",
        message: "This design result is not available. It may belong to a different session.",
      };
    case "not_ready":
      return {
        heading: "Your result is still being prepared",
        message: "This design version is not ready to view yet. Please check back shortly.",
      };
    case "service_unavailable":
      return {
        heading: "Result temporarily unavailable",
        message: "We could not load your result just now. Please try again shortly.",
      };
    case "malformed":
      return {
        heading: "Result temporarily unavailable",
        message: "We received an unexpected response. Please try again shortly.",
      };
    case "unavailable":
      return {
        heading: "Result temporarily unavailable",
        message: "The service could not be reached. Please try again shortly.",
      };
  }
}

// -----------------------------------------------------------------------
// Image delivery failure classification (Phase 11 image endpoint codes)
// -----------------------------------------------------------------------

export class DesignImageQueryError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "DesignImageQueryError";
    this.status = status;
    this.code = code;
  }
}

export type ImageErrorKind = "not_found" | "not_ready" | "delivery_unavailable" | "malformed" | "unavailable";

export function classifyImageError(error: unknown): ImageErrorKind {
  if (!(error instanceof DesignImageQueryError)) return "unavailable";
  switch (error.code) {
    case "not_found":
      return "not_found";
    case "design_image_not_ready":
      return "not_ready";
    case "design_image_delivery_unavailable":
      return "delivery_unavailable";
    case "invalid_response":
      return "malformed";
    default:
      return "unavailable";
  }
}

export function imageErrorCopy(kind: ImageErrorKind): string {
  switch (kind) {
    case "not_found":
      return "The image for this design is not available.";
    case "not_ready":
      return "The image for this design is not ready yet.";
    case "delivery_unavailable":
      return "Image delivery is temporarily unavailable.";
    case "malformed":
      return "We received an unexpected response for the image.";
    case "unavailable":
      return "The image could not be reached. Please try again.";
  }
}
