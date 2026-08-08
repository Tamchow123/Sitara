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
import { TYPE_LABELS, type ItemType, type PaletteName } from "./limits";
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
  onMove: (id: string, delta: Point) => void;
  onRequestEdit: (id: string) => void;
};

type Gesture = {
  type: ItemType;
  start: Point;
  current: Point;
  trail: Point[];
};

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
  imageFailed,
  imageLoading,
  imageUnavailable,
  onImageError,
  onImageLoad,
  onRetryImage,
  onSelect,
  onCreate,
  onMove,
  onRequestEdit,
}: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const boundsRef = useRef<RenderedBounds>({ left: 0, top: 0, width: 0, height: 0 });
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const dragRef = useRef<{ id: string; last: Point } | null>(null);

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
  }, []);

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

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (!overlaysVisible) return;
    const point = pointFrom(event);

    if (tool === "select") {
      // A press on empty canvas clears the selection; a press on a mark is
      // handled by the mark itself and never reaches here.
      if (selectedId) dragRef.current = { id: selectedId, last: point };
      else onSelect(null);
      return;
    }

    event.currentTarget.setPointerCapture?.(event.pointerId);
    setGesture({ type: tool, start: point, current: point, trail: [point] });
  }

  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (drag) {
      const point = pointFrom(event);
      onMove(drag.id, { x: point.x - drag.last.x, y: point.y - drag.last.y });
      dragRef.current = { id: drag.id, last: point };
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

  const stageStyle = {
    aspectRatio: `${imageWidth} / ${imageHeight}`,
    transform: zoom === 1 ? undefined : `scale(${zoom})`,
  };

  return (
    <div className="annotation-canvas" data-tool={tool}>
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

