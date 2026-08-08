// Coordinate maths for the annotation overlay.
//
// Every stored coordinate is NORMALISED to [0, 1] against the version's own
// canonical image dimensions, so a mark keeps its meaning at any rendered size,
// on any device, and across a signed-URL refresh. Nothing here reads the DOM:
// these are pure functions over numbers, which is what makes the whole
// coordinate contract testable without a browser.
//
// The one rule that matters: RESIZING NEVER CHANGES STORED GEOMETRY. Rendered
// bounds convert pointer input INTO normalised space and normalised space back
// out for display, and that is the only direction information flows.

import {
  MIN_ARROW_LENGTH,
  MIN_FREEHAND_POINTS,
  MIN_RECT_EDGE,
  MAX_FREEHAND_POINTS,
  type ItemType,
} from "./limits";
import type { AnnotationGeometry, AnnotationItem, AnnotationPoint } from "@/lib/api";

export type Point = { x: number; y: number };

/** The rendered box a pointer event is measured against. */
export type RenderedBounds = { left: number; top: number; width: number; height: number };

export function clamp01(value: number): number {
  // NaN has no nearest bound and no meaningful comparison, so it collapses to 0
  // rather than propagating into stored geometry and failing validation with a
  // message about a field nobody touched. ±Infinity DOES have a nearest bound
  // and falls through to the comparisons below, so a runaway coordinate lands on
  // the edge it ran off rather than jumping to the opposite corner.
  if (Number.isNaN(value)) return 0;
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

/**
 * A client pointer position as normalised image coordinates.
 *
 * Derived from the bounds the caller measured, never from a hard-coded size —
 * the image is responsive and capped by `min(64vh, 620px)`, so its rendered
 * size is not knowable ahead of time. A zero-sized box (not yet laid out, or
 * display:none) returns the origin rather than dividing by zero.
 */
export function toNormalised(
  clientX: number,
  clientY: number,
  bounds: RenderedBounds,
): Point {
  if (bounds.width <= 0 || bounds.height <= 0) return { x: 0, y: 0 };
  return {
    x: clamp01((clientX - bounds.left) / bounds.width),
    y: clamp01((clientY - bounds.top) / bounds.height),
  };
}

/**
 * Stroke widths, badge radii and handle sizes are constants in the SVG's own
 * user space, scaled from the handoff's 600x800 reference. Without this a mark
 * drawn on a 2048px render would appear a third the size of the same mark on a
 * 600px one, because the viewBox scales everything with the image.
 */
export function overlayScale(imageWidth: number, imageHeight: number): number {
  const smaller = Math.min(imageWidth, imageHeight);
  if (!Number.isFinite(smaller) || smaller <= 0) return 1;
  return smaller / 600;
}

/** A nudge is a fraction of the SMALLER edge, so it moves the same visible
 * distance horizontally and vertically on a non-square image. */
export function nudgeDelta(
  step: number,
  imageWidth: number,
  imageHeight: number,
): Point {
  const smaller = Math.min(imageWidth, imageHeight);
  if (!Number.isFinite(smaller) || smaller <= 0) return { x: 0, y: 0 };
  return { x: (step * smaller) / imageWidth, y: (step * smaller) / imageHeight };
}

// ---------------------------------------------------------------------------
// Per-type geometry
// ---------------------------------------------------------------------------

export function pointsOf(geometry: AnnotationGeometry): AnnotationPoint[] {
  if ("point" in geometry) return [geometry.point];
  if ("start" in geometry) return [geometry.start, geometry.end];
  if ("width" in geometry) {
    // Origin plus extent, so the far corner is derived. Both are returned
    // because the server also bounds `x + width` and `y + height`.
    return [
      { x: geometry.x, y: geometry.y },
      { x: geometry.x + geometry.width, y: geometry.y + geometry.height },
    ];
  }
  if ("points" in geometry) return geometry.points;
  return [];
}

/** The point a number badge and note chip hang off. */
export function anchorOf(geometry: AnnotationGeometry): Point {
  if ("point" in geometry) return geometry.point;
  if ("start" in geometry) return geometry.end;
  if ("width" in geometry) return { x: geometry.x, y: geometry.y };
  if ("points" in geometry) {
    const points = geometry.points;
    return points[0] ?? { x: 0, y: 0 };
  }
  return { x: 0, y: 0 };
}

/**
 * Move a whole mark, clamped so no part of it leaves the image.
 *
 * The clamp is applied to the WHOLE shape rather than per point: clamping each
 * point independently would silently deform a rectangle or arrow that ran off
 * an edge, changing what the stylist drew instead of just refusing to move it
 * further.
 */
export function translateGeometry(
  geometry: AnnotationGeometry,
  delta: Point,
): AnnotationGeometry {
  const points = pointsOf(geometry);
  if (points.length === 0) return geometry;

  let dx = delta.x;
  let dy = delta.y;
  for (const point of points) {
    dx = Math.min(dx, 1 - point.x);
    dx = Math.max(dx, -point.x);
    dy = Math.min(dy, 1 - point.y);
    dy = Math.max(dy, -point.y);
  }

  const move = (point: AnnotationPoint): AnnotationPoint => ({
    x: clamp01(point.x + dx),
    y: clamp01(point.y + dy),
  });

  if ("point" in geometry) return { point: move(geometry.point) };
  if ("start" in geometry) return { start: move(geometry.start), end: move(geometry.end) };
  if ("width" in geometry) {
    // Only the origin moves; the extent is carried unchanged, so a rectangle
    // pushed against an edge stops rather than being squashed.
    const moved = move({ x: geometry.x, y: geometry.y });
    return {
      x: moved.x,
      y: moved.y,
      width: geometry.width,
      height: geometry.height,
    };
  }
  if ("points" in geometry) return { points: geometry.points.map(move) };
  return geometry;
}

// ---------------------------------------------------------------------------
// Building a mark from a gesture
// ---------------------------------------------------------------------------

export function arrowLength(start: Point, end: Point): number {
  return Math.hypot(end.x - start.x, end.y - start.y);
}

/** A rectangle from two dragged corners, in either direction. */
export function rectFromCorners(a: Point, b: Point) {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    width: Math.abs(b.x - a.x),
    height: Math.abs(b.y - a.y),
  };
}

/**
 * Reduce a raw pointer trail to something the schema accepts.
 *
 * Freehand is capped at 500 points, and a drawing gesture easily produces
 * thousands. Dropping the tail would truncate the stroke, so this keeps every
 * point that carries shape and thins evenly when it must — the stroke stays the
 * shape that was drawn, at lower resolution.
 */
export function simplifyStroke(points: Point[], maxPoints = MAX_FREEHAND_POINTS): Point[] {
  if (points.length <= maxPoints) return points;
  const kept: Point[] = [];
  const stride = (points.length - 1) / (maxPoints - 1);
  for (let index = 0; index < maxPoints; index += 1) {
    kept.push(points[Math.round(index * stride)]!);
  }
  // Guarantee the true endpoint survives rounding, so a closed shape still
  // closes and a stroke still ends where the finger lifted.
  kept[kept.length - 1] = points[points.length - 1]!;
  return kept;
}

/**
 * Is this gesture a mark, or a mis-click?
 *
 * Returns null for a degenerate shape rather than creating something the server
 * will reject — the same tolerances the server applies, checked before the
 * document is ever built.
 */
export function geometryFromGesture(
  type: ItemType,
  start: Point,
  end: Point,
  trail: Point[],
): AnnotationGeometry | null {
  switch (type) {
    case "pin":
      return { point: { x: clamp01(start.x), y: clamp01(start.y) } };
    case "arrow": {
      if (arrowLength(start, end) < MIN_ARROW_LENGTH) return null;
      return { start, end };
    }
    case "rectangle": {
      const rect = rectFromCorners(start, end);
      if (rect.width < MIN_RECT_EDGE || rect.height < MIN_RECT_EDGE) return null;
      return rect;
    }
    case "freehand": {
      const simplified = simplifyStroke(trail);
      if (simplified.length < MIN_FREEHAND_POINTS) return null;
      return { points: simplified };
    }
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Describing a mark without the canvas (§13)
// ---------------------------------------------------------------------------

function percent(value: number): number {
  return Math.round(clamp01(value) * 100);
}

/**
 * A geometry summary for someone who cannot see the overlay.
 *
 * The visual layer is not allowed to be the only way to understand where a mark
 * is, so every row carries a positional description in words. Phrased from the
 * top-left because that is how the rest of the interface reads.
 */
export function describeGeometry(geometry: AnnotationGeometry): string {
  if ("point" in geometry) {
    return `centred at ${percent(geometry.point.x)}% across, ${percent(geometry.point.y)}% from the top`;
  }
  if ("start" in geometry) {
    return (
      `from ${percent(geometry.start.x)}% across, ${percent(geometry.start.y)}% down ` +
      `to ${percent(geometry.end.x)}% across, ${percent(geometry.end.y)}% down`
    );
  }
  if ("width" in geometry) {
    const { x, y, width, height } = geometry;
    return (
      `${percent(width)}% wide by ${percent(height)}% tall, ` +
      `starting ${percent(x)}% across and ${percent(y)}% from the top`
    );
  }
  if ("points" in geometry) {
    const first = geometry.points[0];
    if (!first) return "a freehand stroke";
    return (
      `a freehand stroke of ${geometry.points.length} points, ` +
      `starting ${percent(first.x)}% across and ${percent(first.y)}% from the top`
    );
  }
  return "an unrecognised shape";
}

/**
 * The accessible name for a mark, in the form §13 specifies.
 *
 * A mark with no note says so explicitly — an empty note is allowed by the
 * schema, and a label that just stopped after the type would leave a
 * screen-reader user unable to tell "no note" from "the label is broken".
 */
export function describeItem(item: AnnotationItem, typeLabel: string): string {
  const note = item.note.trim();
  const head = `Annotation ${item.created_order}, ${typeLabel.toLowerCase()}`;
  return note ? `${head}: ${note}` : `${head}, no note`;
}
