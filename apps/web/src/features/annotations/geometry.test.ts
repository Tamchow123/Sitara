import { describe, expect, it } from "vitest";

import {
  anchorOf,
  arrowLength,
  clamp01,
  describeGeometry,
  describeItem,
  geometryFromGesture,
  nudgeDelta,
  overlayScale,
  pointsOf,
  rectFromCorners,
  simplifyStroke,
  toNormalised,
  translateGeometry,
} from "./geometry";
import { MAX_FREEHAND_POINTS, MIN_ARROW_LENGTH, MIN_RECT_EDGE } from "./limits";
import type { AnnotationItem } from "@/lib/api";

describe("normalising pointer input", () => {
  it("maps a press to the same normalised point whatever the rendered size", () => {
    // THE coordinate invariant: a mark placed at the centre of a 400px-wide
    // render and the centre of a 1200px-wide render is the same mark. If this
    // ever depended on rendered size, every stored coordinate would drift the
    // first time a panel opened.
    const small = { left: 0, top: 0, width: 400, height: 600 };
    const large = { left: 0, top: 0, width: 1200, height: 1800 };

    expect(toNormalised(200, 300, small)).toEqual(toNormalised(600, 900, large));
  });

  it("measures from the box's own offset, not the viewport", () => {
    const bounds = { left: 120, top: 80, width: 400, height: 400 };
    expect(toNormalised(320, 280, bounds)).toEqual({ x: 0.5, y: 0.5 });
  });

  it("clamps a press outside the image rather than storing an out-of-range value", () => {
    // The server rejects anything outside [0, 1]; a drag that leaves the image
    // should stop at the edge, not make the document unsaveable.
    const bounds = { left: 0, top: 0, width: 200, height: 200 };
    expect(toNormalised(-50, 400, bounds)).toEqual({ x: 0, y: 1 });
  });

  it("returns the origin for a zero-sized box instead of dividing by zero", () => {
    expect(toNormalised(10, 10, { left: 0, top: 0, width: 0, height: 0 })).toEqual({
      x: 0,
      y: 0,
    });
  });
});

describe("clamp01", () => {
  it("collapses NaN to zero rather than letting it reach stored geometry", () => {
    // NaN has no nearest bound and no meaningful comparison. Left alone it would
    // fail server validation with a message about a field nobody touched.
    expect(clamp01(Number.NaN)).toBe(0);
  });

  it("clamps an infinity to the edge it ran off, not the opposite corner", () => {
    // The first version of this returned 0 for BOTH infinities, which would have
    // teleported a runaway coordinate to the top-left.
    expect(clamp01(Number.POSITIVE_INFINITY)).toBe(1);
    expect(clamp01(Number.NEGATIVE_INFINITY)).toBe(0);
  });
});

describe("overlay scale", () => {
  it("keeps a mark the same apparent size on a small and a large render", () => {
    expect(overlayScale(600, 800)).toBe(1);
    expect(overlayScale(1200, 1600)).toBe(2);
  });

  it("falls back to 1 for a degenerate size rather than collapsing every mark", () => {
    expect(overlayScale(0, 0)).toBe(1);
  });
});

describe("nudge steps", () => {
  it("moves the same visible distance on both axes of a non-square image", () => {
    // A flat 0.005 on both axes would move a mark further vertically than
    // horizontally on a portrait render, which feels broken under the arrow keys.
    const delta = nudgeDelta(0.01, 1000, 2000);
    expect(delta.x * 1000).toBeCloseTo(delta.y * 2000);
  });
});

describe("building geometry from a gesture", () => {
  const at = (x: number, y: number) => ({ x, y });

  it("makes a pin from a single point", () => {
    expect(geometryFromGesture("pin", at(0.5, 0.25), at(0.5, 0.25), [])).toEqual({
      point: { x: 0.5, y: 0.25 },
    });
  });

  it("refuses an arrow shorter than the tolerance, measured diagonally", () => {
    // hypot, not either axis alone: a drag of 0.004 on BOTH axes is longer than
    // 0.005 overall and is a real arrow, while 0.004 on one axis is not.
    const tiny = geometryFromGesture("arrow", at(0.5, 0.5), at(0.5 + 0.004, 0.5), []);
    expect(tiny).toBeNull();

    const diagonal = geometryFromGesture("arrow", at(0.5, 0.5), at(0.504, 0.504), []);
    expect(diagonal).not.toBeNull();
    expect(arrowLength(at(0.5, 0.5), at(0.504, 0.504))).toBeGreaterThan(MIN_ARROW_LENGTH);
  });

  it("refuses a rectangle with no visible area", () => {
    expect(geometryFromGesture("rectangle", at(0.2, 0.2), at(0.2, 0.9), [])).toBeNull();
  });

  it("builds a rectangle as origin plus extent, dragged in any direction", () => {
    // The wire shape is origin+extent so one shape has exactly one
    // representation; dragging up-left must produce the same rectangle as
    // dragging down-right.
    const downRight = geometryFromGesture("rectangle", at(0.2, 0.3), at(0.6, 0.8), []);
    const upLeft = geometryFromGesture("rectangle", at(0.6, 0.8), at(0.2, 0.3), []);

    // Both directions must produce the IDENTICAL object, which is the property
    // that matters. The individual extents are compared numerically because
    // 0.6 - 0.2 is not exactly 0.4 in IEEE 754.
    expect(downRight).toEqual(upLeft);
    const rect = downRight as { x: number; y: number; width: number; height: number };
    expect(rect.x).toBeCloseTo(0.2);
    expect(rect.y).toBeCloseTo(0.3);
    expect(rect.width).toBeCloseTo(0.4);
    expect(rect.height).toBeCloseTo(0.5);
  });

  it("refuses a freehand stroke of one point", () => {
    expect(geometryFromGesture("freehand", at(0.1, 0.1), at(0.1, 0.1), [at(0.1, 0.1)])).toBeNull();
  });

  it("rejects a rectangle exactly under the edge tolerance", () => {
    const under = MIN_RECT_EDGE / 2;
    expect(
      geometryFromGesture("rectangle", at(0.1, 0.1), at(0.1 + under, 0.9), []),
    ).toBeNull();
  });
});

describe("simplifying a freehand stroke", () => {
  it("leaves a short stroke untouched", () => {
    const points = [
      { x: 0, y: 0 },
      { x: 0.5, y: 0.5 },
    ];
    expect(simplifyStroke(points)).toBe(points);
  });

  it("thins a long stroke to the cap while keeping both ends", () => {
    // A drawing gesture easily produces thousands of points. Truncating would
    // cut the stroke short; thinning keeps the shape at lower resolution.
    const points = Array.from({ length: 4000 }, (_, index) => ({
      x: index / 3999,
      y: 0.5,
    }));
    const simplified = simplifyStroke(points);

    expect(simplified).toHaveLength(MAX_FREEHAND_POINTS);
    expect(simplified[0]).toEqual(points[0]);
    expect(simplified[simplified.length - 1]).toEqual(points[points.length - 1]);
  });
});

describe("translating a mark", () => {
  it("moves a rectangle without deforming it at the edge", () => {
    // Clamping each corner independently would squash the rectangle against the
    // edge, silently changing what the stylist drew.
    const geometry = { x: 0.8, y: 0.1, width: 0.2, height: 0.2 };
    const moved = translateGeometry(geometry, { x: 0.5, y: 0 });

    expect(moved).toEqual({ x: 0.8, y: 0.1, width: 0.2, height: 0.2 });
  });

  it("moves an arrow as one shape, keeping its length", () => {
    const geometry = { start: { x: 0.1, y: 0.1 }, end: { x: 0.3, y: 0.4 } };
    const moved = translateGeometry(geometry, { x: 0.1, y: 0.1 }) as typeof geometry;

    expect(arrowLength(moved.start, moved.end)).toBeCloseTo(arrowLength(geometry.start, geometry.end));
    expect(moved.start).toEqual({ x: 0.2, y: 0.2 });
  });

  it("stops at the edge rather than clipping one end of an arrow", () => {
    const geometry = { start: { x: 0.9, y: 0.5 }, end: { x: 0.95, y: 0.5 } };
    const moved = translateGeometry(geometry, { x: 0.5, y: 0 }) as typeof geometry;

    expect(moved.end.x).toBeCloseTo(1);
    expect(moved.start.x).toBeCloseTo(0.95);
  });
});

describe("points and anchors", () => {
  it("reports both rectangle corners, since the server bounds the far one too", () => {
    const [origin, far] = pointsOf({ x: 0.2, y: 0.3, width: 0.4, height: 0.5 });
    expect(origin).toEqual({ x: 0.2, y: 0.3 });
    expect(far!.x).toBeCloseTo(0.6);
    expect(far!.y).toBeCloseTo(0.8);
  });

  it("anchors an arrow's badge at its head, where it points", () => {
    expect(anchorOf({ start: { x: 0.1, y: 0.1 }, end: { x: 0.7, y: 0.2 } })).toEqual({
      x: 0.7,
      y: 0.2,
    });
  });
});

describe("describing a mark without the canvas", () => {
  it("gives a positional summary in words for each type", () => {
    expect(describeGeometry({ point: { x: 0.5, y: 0.18 } })).toBe(
      "centred at 50% across, 18% from the top",
    );
    expect(describeGeometry({ x: 0.1, y: 0.2, width: 0.3, height: 0.4 })).toContain(
      "30% wide by 40% tall",
    );
    expect(
      describeGeometry({ start: { x: 0, y: 0 }, end: { x: 1, y: 1 } }),
    ).toBe("from 0% across, 0% down to 100% across, 100% down");
    expect(
      describeGeometry({
        points: [
          { x: 0.2, y: 0.2 },
          { x: 0.4, y: 0.4 },
        ],
      }),
    ).toContain("2 points");
  });

  it("says 'no note' explicitly rather than trailing off", () => {
    // An empty note is allowed by the schema. A label that just stopped after
    // the type would leave a screen-reader user unable to tell "no note" from
    // "the label is broken".
    const item = {
      id: "a",
      type: "rectangle",
      geometry: { x: 0, y: 0, width: 0.5, height: 0.5 },
      note: "   ",
      palette: "sage",
      created_order: 3,
    } as AnnotationItem;

    expect(describeItem(item, "Rectangle")).toBe("Annotation 3, rectangle, no note");
  });

  it("reads the note when there is one", () => {
    const item = {
      id: "a",
      type: "rectangle",
      geometry: { x: 0, y: 0, width: 0.5, height: 0.5 },
      note: "Champagne hem border is too narrow here",
      palette: "sage",
      created_order: 3,
    } as AnnotationItem;

    expect(describeItem(item, "Rectangle")).toBe(
      "Annotation 3, rectangle: Champagne hem border is too narrow here",
    );
  });
});

describe("rectFromCorners", () => {
  it("normalises either drag direction to one representation", () => {
    const rect = rectFromCorners({ x: 0.6, y: 0.7 }, { x: 0.2, y: 0.1 });
    expect(rect.x).toBeCloseTo(0.2);
    expect(rect.y).toBeCloseTo(0.1);
    expect(rect.width).toBeCloseTo(0.4);
    expect(rect.height).toBeCloseTo(0.6);
  });
});
