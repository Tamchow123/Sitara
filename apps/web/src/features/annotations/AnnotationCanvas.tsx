"use client";

// The image and its overlay.
//
// The overlay is an inline SVG whose viewBox IS the image's own pixel space, so
// normalised geometry maps by a plain multiply and needs no layout knowledge.
// The image box is sized from the version's intrinsic dimensions via
// `aspect-ratio`, and NEVER `object-fit: cover` — a cover box crops, and a crop
// silently invalidates every stored coordinate while looking perfectly fine.
//
// Pointer input is the only place rendered size matters, and it is measured from
// `getBoundingClientRect()` tracked with a ResizeObserver rather than assumed.
// Resizing therefore changes nothing that is stored.
//
// Zoom and pan transform the image and the overlay TOGETHER, as one wrapper, so
// marks stay pinned to the garment and geometry is never rewritten to fake it.
// Both are pure view state and neither is ever stored: a pan offset is client
// pixels of the viewport, which has no meaning in a document read on another
// screen. Because `getBoundingClientRect()` reports the TRANSFORMED box, pointer
// input converts correctly under any zoom or pan with no extra arithmetic — the
// transform is deliberately kept to translate and scale so that stays true.
//
// Gestures, in one place so their precedence is legible:
//
//   press on a mark, select tool   -> select it and move it, one gesture
//   press on a mark, drawing tool  -> select only (unchanged)
//   press on empty, select, zoomed -> deselect, then pan
//   press on empty, select, fitted -> deselect
//   press on empty, drawing tool   -> draw a new mark

import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { PinGlyph } from "./Glyphs";
import { MARK_STROKE, MarkShape } from "./MarkShapes";
import {
  geometryFromGesture,
  overlayScale,
  toNormalised,
  type Point,
  type RenderedBounds,
} from "./geometry";
import {
  DRAG_THRESHOLD_PX,
  TYPE_LABELS,
  type ItemType,
  type PaletteName,
} from "./limits";
import { describeItem } from "./geometry";
import type { AnnotationGeometry, AnnotationItem } from "@/lib/api";

export type Tool = "select" | ItemType;

type Props = {
  imageUrl: string | null;
  imageWidth: number;
  imageHeight: number;
  altText: string;
  items: AnnotationItem[];
  selectedId: string | null;
  tool: Tool;
  palette: PaletteName;
  overlaysVisible: boolean;
  zoom: number;
  /** Viewport offset in client pixels. View state only — never stored. */
  pan: Point;
  /** The <img> element itself failed to load a URL we did have. */
  imageFailed: boolean;
  /** The URL fetch is still in flight. */
  imageLoading: boolean;
  /** The URL fetch failed outright, so there is no src to try. */
  imageUnavailable: boolean;
  onImageError: () => void;
  onImageLoad: () => void;
  onRetryImage: () => void;
  onSelect: (id: string | null) => void;
  onCreate: (type: ItemType, geometry: AnnotationGeometry) => void;
  /**
   * One step of a drag. `gesture` is constant for the whole drag so the reducer
   * can collapse it into a single undo step.
   */
  onMoveBy: (id: string, delta: Point, gesture: number) => void;
  /** A pan step, in client pixels. The parent clamps and stores it. */
  onPanBy: (delta: Point) => void;
  /**
   * The stage's UNSCALED layout size, reported as it changes.
   *
   * `offsetWidth`/`offsetHeight`, not the bounding rect: the rect is the
   * transformed box, so at zoom 2 it reads double and a pan limit derived from it
   * would be twice the region the zoom actually hid.
   */
  onStageSize: (size: { width: number; height: number }) => void;
  onRequestEdit: (id: string) => void;
};

type Gesture = {
  type: ItemType;
  start: Point;
  current: Point;
  trail: Point[];
};

/**
 * What the pointer is currently doing, when it is not drawing.
 *
 * A press on a mark arms a `mark` drag; a press on empty canvas while zoomed arms
 * a `pan`. They are one union rather than two refs because they are mutually
 * exclusive by construction, and two independent nullable refs would allow the
 * state that must never exist: moving a mark and panning under it at once, which
 * would move the mark by the sum of both.
 */
type Drag =
  | {
      kind: "mark";
      id: string;
      /** Last position in normalised space, to difference the next move against. */
      last: Point;
      /** Where the press landed, in client px, for the movement threshold. */
      origin: Point;
      gesture: number;
      moved: boolean;
    }
  | { kind: "pan"; last: Point };

export function AnnotationCanvas({
  imageUrl,
  imageWidth,
  imageHeight,
  altText,
  items,
  selectedId,
  tool,
  palette,
  overlaysVisible,
  zoom,
  pan,
  imageFailed,
  imageLoading,
  imageUnavailable,
  onImageError,
  onImageLoad,
  onRetryImage,
  onSelect,
  onCreate,
  onMoveBy,
  onPanBy,
  onStageSize,
  onRequestEdit,
}: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const boundsRef = useRef<RenderedBounds>({ left: 0, top: 0, width: 0, height: 0 });
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const [panning, setPanning] = useState(false);
  const gestureIdRef = useRef(0);

  // Read through a ref by the one callback that must stay referentially stable
  // across a tool change: `grabMark` is handed to every memoised `MarkShape`, and
  // a new function identity there rebuilds all of them.
  const toolRef = useRef(tool);
  toolRef.current = tool;

  const measure = useCallback(() => {
    const node = stageRef.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    boundsRef.current = {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
    };
    onStageSize({ width: node.offsetWidth, height: node.offsetHeight });
  }, [onStageSize]);

  useEffect(() => {
    measure();
    const node = stageRef.current;
    if (!node) return;

    // ResizeObserver catches layout changes the window never hears about — a
    // panel opening, the list growing, a zoom step. Scroll moves the box without
    // resizing it, so that is tracked separately.
    //
    // Guarded because it is not universal (and jsdom has none). Losing it is not
    // a correctness failure: every pointer press re-measures before converting,
    // so the observer is an optimisation that keeps the cached bounds warm, not
    // the thing that makes coordinates right.
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(node);
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
    };
  }, [measure]);

  const scale = overlayScale(imageWidth, imageHeight);

  function pointFrom(event: ReactPointerEvent<Element>): Point {
    // Re-measured on the press rather than trusted from the observer: a zoom or
    // pan applied since the last measurement moves the box without resizing it.
    measure();
    return toNormalised(event.clientX, event.clientY, boundsRef.current);
  }

  /**
   * Try to take pointer capture, and never let failing at it break the gesture.
   *
   * Capture keeps the closing `pointerup` coming back to the stage after the
   * pointer has travelled off it, which matters because `finishGesture` is what
   * disarms a drag. It is an OPTIMISATION for gesture completion, not a
   * correctness requirement: where it is unavailable (jsdom has no such method)
   * or refused (`NotFoundError` for a pointer id that is not active), dragging
   * still has to work, so this swallows both and the gesture proceeds.
   *
   * What makes that safe is that a drag left armed is now recovered from at both
   * ways back in: the next movement with no button held clears it before it can
   * move anything, and a fresh press clears it outright. Refusing to drag at all
   * on a failed capture was the first version of this and was a bad trade — it
   * gave up the whole feature to avoid a state that is already handled.
   *
   * Deliberately NOT optional-chained on the call: an uncaught DOMException here
   * escapes the React event handler, which is how this was written first.
   */
  const capturePointer = useCallback((pointerId: number) => {
    try {
      stageRef.current?.setPointerCapture?.(pointerId);
    } catch {
      // See above: gesture completion degrades, correctness does not depend on it.
    }
  }, []);

  /**
   * A press on a mark: select it and arm a move in the same gesture.
   *
   * Stable across tool and zoom changes (both read through refs) because every
   * memoised `MarkShape` receives this identity.
   */
  const grabMark = useCallback(
    (id: string, event: ReactPointerEvent<Element>) => {
      // Stopped here rather than in the mark, so one place decides what a press on
      // a mark means: never a new-mark gesture, never the start of a pan.
      event.stopPropagation();
      onSelect(id);
      // Only the select tool moves marks. With a drawing tool active a press on a
      // mark still just selects it, which is the behaviour that was already there.
      if (toolRef.current !== "select") return;
      measure();
      dragRef.current = {
        kind: "mark",
        id,
        last: toNormalised(event.clientX, event.clientY, boundsRef.current),
        origin: { x: event.clientX, y: event.clientY },
        gesture: (gestureIdRef.current += 1),
        moved: false,
      };
      // Captured on the STAGE, not the mark: a mark is a few pixels wide and the
      // pointer leaves it on the first real movement. Without this the drag would
      // stop the instant it started working.
      capturePointer(event.pointerId);
    },
    [onSelect, measure, capturePointer],
  );

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (!overlaysVisible) return;
    // A fresh press owns the drag state outright. Anything left armed by a
    // gesture that never received its `pointerup` must not leak into this one and
    // start moving a mark the user pressed nowhere near.
    dragRef.current = null;
    const point = pointFrom(event);

    if (tool === "select") {
      // Empty canvas. A press on a mark never reaches here — `grabMark` stops it.
      //
      // It used to start dragging whatever was selected, from anywhere on the
      // canvas: pressing empty space with a mark selected moved that mark instead
      // of deselecting it, and the mark was often nowhere near the pointer. Now
      // empty space means what it looks like — drop the selection, and pan if
      // there is anything hidden to pan to.
      onSelect(null);
      if (zoom > 1) {
        dragRef.current = { kind: "pan", last: { x: event.clientX, y: event.clientY } };
        setPanning(true);
        capturePointer(event.pointerId);
      }
      return;
    }

    capturePointer(event.pointerId);
    setGesture({ type: tool, start: point, current: point, trail: [point] });
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;

    // Nothing is being held, so this is a hover, not a drag — however the drag
    // came to still be armed. Treating it as a continuation is the difference
    // between a stuck gesture and marks that visibly wander under the cursor
    // while writing themselves to the server, which is far worse than a lost
    // drag. `buttons` is 1 throughout a real mouse drag and throughout touch and
    // pen contact, so this only ever catches the case it is meant to.
    if (drag && event.buttons === 0) {
      dragRef.current = null;
      setPanning(false);
      return;
    }

    if (drag?.kind === "pan") {
      onPanBy({ x: event.clientX - drag.last.x, y: event.clientY - drag.last.y });
      dragRef.current = { kind: "pan", last: { x: event.clientX, y: event.clientY } };
      return;
    }

    if (drag?.kind === "mark") {
      // Below the threshold this press is still just a click. Moving on the first
      // stray pixel would mean selecting a mark to read its note nudged it and
      // left the document unsaved.
      const travelled = Math.hypot(
        event.clientX - drag.origin.x,
        event.clientY - drag.origin.y,
      );
      if (!drag.moved && travelled < DRAG_THRESHOLD_PX) return;
      const point = pointFrom(event);
      onMoveBy(
        drag.id,
        { x: point.x - drag.last.x, y: point.y - drag.last.y },
        drag.gesture,
      );
      dragRef.current = { ...drag, last: point, moved: true };
      return;
    }

    if (!gesture) return;
    const point = pointFrom(event);
    setGesture({
      ...gesture,
      current: point,
      trail: gesture.type === "freehand" ? [...gesture.trail, point] : gesture.trail,
    });
  }

  function finishGesture(event: ReactPointerEvent<HTMLDivElement>) {
    dragRef.current = null;
    setPanning(false);
    if (!gesture) return;
    const point = pointFrom(event);
    const geometry = geometryFromGesture(
      gesture.type,
      gesture.start,
      point,
      [...gesture.trail, point],
    );
    setGesture(null);
    // A degenerate shape produces nothing rather than a mark the server would
    // reject — a mis-click should feel like a mis-click, not an error.
    if (geometry) onCreate(gesture.type, geometry);
  }

  // `translate` before `scale`, so the offset is in unscaled client pixels and
  // means the same thing `clampPan` computed it in. Written the other way round it
  // would be multiplied by the zoom and overshoot at every step above 1.
  const panned = pan.x !== 0 || pan.y !== 0;
  const stageStyle = {
    aspectRatio: `${imageWidth} / ${imageHeight}`,
    transform:
      zoom === 1 && !panned ? undefined : `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
  };

  return (
    <div
      className="annotation-canvas"
      data-tool={tool}
      // Drives the cursor: `grab` only when there is something hidden to pan to,
      // rather than promising a pan at zoom 1 where nothing can move.
      data-pannable={tool === "select" && zoom > 1 ? "true" : undefined}
      data-panning={panning ? "true" : undefined}
    >
      {!overlaysVisible && (
        <p className="annotation-hidden-pill">Overlays hidden — the eye brings them back</p>
      )}

      <div className="annotation-stage-wrap">
        <div
          className="annotation-stage"
          ref={stageRef}
          style={stageStyle}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={finishGesture}
          onPointerCancel={finishGesture}
        >
          {imageUrl && !imageFailed ? (
            /* eslint-disable-next-line @next/next/no-img-element -- short-lived
               signed URL, deliberately outside next/image's remote cache */
            <img
              className="annotation-image"
              src={imageUrl}
              alt={altText}
              width={imageWidth}
              height={imageHeight}
              draggable={false}
              referrerPolicy="no-referrer"
              onError={onImageError}
              onLoad={onImageLoad}
            />
          ) : (
            // The overlay and the list stay live over this placeholder: a failed
            // image must not destroy notes that have not been saved yet.
            //
            // Three distinct states, not two. Before this, a FAILED URL fetch
            // produced no `src` at all, so the <img> never mounted, its onError
            // never fired, and the canvas sat on "Loading…" for ever with no way
            // out — indistinguishable from a slow network and unrecoverable
            // without a full reload.
            <div
              className="annotation-image-missing"
              role={imageLoading ? "status" : "alert"}
              aria-live={imageLoading ? "polite" : undefined}
            >
              {imageLoading ? (
                <p>Loading your concept image…</p>
              ) : (
                <>
                  {/* Three causes, named separately, because "it didn't work" tells
                      the user nothing about whether retrying is worth it. */}
                  <p>
                    {imageFailed
                      ? "The concept image could not be loaded. Your marks and notes are safe."
                      : imageUnavailable
                        ? "The link to your concept image could not be fetched. Your marks and notes are safe."
                        : "Your concept image is not available right now. Your marks and notes are safe."}
                  </p>
                  <button type="button" className="btn btn-secondary" onClick={onRetryImage}>
                    Retry image
                  </button>
                </>
              )}
            </div>
          )}

          {overlaysVisible && (
            <svg
              className="annotation-overlay"
              viewBox={`0 0 ${imageWidth} ${imageHeight}`}
              preserveAspectRatio="xMidYMid meet"
              aria-hidden="true"
            >
              {items.map((item) => (
                <MarkShape
                  key={item.id}
                  item={item}
                  imageWidth={imageWidth}
                  imageHeight={imageHeight}
                  scale={scale}
                  selected={item.id === selectedId}
                  onSelect={onSelect}
                  onGrab={grabMark}
                  onRequestEdit={onRequestEdit}
                  label={describeItem(item, TYPE_LABELS[item.type])}
                />
              ))}
              {gesture && (
                <PreviewShape
                  gesture={gesture}
                  imageWidth={imageWidth}
                  imageHeight={imageHeight}
                  scale={scale}
                  palette={palette}
                />
              )}
            </svg>
          )}
        </div>
      </div>

      {items.length === 0 && overlaysVisible && !gesture && (
        <div className="annotation-empty-card">
          <span className="annotation-empty-glyph" aria-hidden="true">
            <PinGlyph size={22} />
          </span>
          <h2>Nothing marked yet</h2>
          <p>
            Pick a tool on the left, then click anywhere on the render. Every mark takes a
            short note.
          </p>
        </div>
      )}
    </div>
  );
}

// The in-progress shape, drawn with the same halo-then-colour rule so what a
// gesture looks like while it is being made matches what it becomes.
function PreviewShape({
  gesture,
  imageWidth,
  imageHeight,
  scale,
  palette,
}: {
  gesture: Gesture;
  imageWidth: number;
  imageHeight: number;
  scale: number;
  palette: PaletteName;
}) {
  const px = (value: number) => value * imageWidth;
  const py = (value: number) => value * imageHeight;
  const s = (value: number) => value * scale;
  const { type, start, current, trail } = gesture;

  if (type === "pin") return null;

  if (type === "arrow") {
    return (
      <g className={`mark mark-${palette} is-preview`}>
        <line
          x1={px(start.x)}
          y1={py(start.y)}
          x2={px(current.x)}
          y2={py(current.y)}
          stroke="var(--color-on-accent)"
          strokeWidth={s(MARK_STROKE.haloLine)}
          strokeLinecap="round"
        />
        <line
          className="mark-stroke"
          x1={px(start.x)}
          y1={py(start.y)}
          x2={px(current.x)}
          y2={py(current.y)}
          strokeWidth={s(MARK_STROKE.colour)}
          strokeLinecap="round"
        />
      </g>
    );
  }

  if (type === "rectangle") {
    const x = px(Math.min(start.x, current.x));
    const y = py(Math.min(start.y, current.y));
    const width = Math.abs(px(current.x) - px(start.x));
    const height = Math.abs(py(current.y) - py(start.y));
    return (
      <g className={`mark mark-${palette} is-preview`}>
        <rect
          x={x}
          y={y}
          width={width}
          height={height}
          rx={s(MARK_STROKE.rectRadius)}
          fill="none"
          stroke="var(--color-on-accent)"
          strokeWidth={s(MARK_STROKE.haloRect)}
        />
        <rect
          className="mark-stroke"
          x={x}
          y={y}
          width={width}
          height={height}
          rx={s(MARK_STROKE.rectRadius)}
          fill="none"
          strokeWidth={s(MARK_STROKE.colour)}
        />
      </g>
    );
  }

  const path = trail.map((point) => `${px(point.x)},${py(point.y)}`).join(" ");
  return (
    <g className={`mark mark-${palette} is-preview`}>
      <polyline
        points={path}
        fill="none"
        stroke="var(--color-on-accent)"
        strokeWidth={s(MARK_STROKE.haloLine)}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <polyline
        className="mark-stroke"
        points={path}
        fill="none"
        strokeWidth={s(MARK_STROKE.colour)}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </g>
  );
}

