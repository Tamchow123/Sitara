"use client";

// One mark, drawn in the overlay's user space.
//
// Every stroke is drawn TWICE: a white halo underneath, then the palette colour
// on top. That is an accessibility requirement, not styling — a sage line over
// pale silk and an ink line over a black lehenga are both invisible without it,
// and the palette is a labelled user choice we cannot second-guess. The server's
// PNG composition does the same thing for the same reason, so a mark looks the
// same in the browser and in the emailed render.
//
// Sizes are constants scaled by `min(W, H) / 600` from the handoff's 600x800
// reference, so a mark reads at the same apparent size on a 600px render and a
// 2048px one. Colours come from CSS custom properties via a per-palette class,
// so the palette lives in one place rather than being duplicated as hex here.

import { memo } from "react";

import { anchorOf } from "./geometry";
import type { AnnotationItem } from "@/lib/api";

const HALO = "var(--color-on-accent)";

/**
 * The stroke geometry every mark is drawn with, in the 600px reference space that
 * `overlayScale` scales from.
 *
 * Exported because the in-progress preview in `AnnotationCanvas` draws the same
 * shapes with the same halo-then-colour rule. When these were separate literals in
 * the two files, a gesture could look one weight while being made and another the
 * instant it became a mark, and nothing would have caught the drift.
 */
export const MARK_STROKE = {
  /** White underlay, so a mark stays visible over a light OR a dark garment. */
  haloLine: 7,
  haloRect: 6,
  haloPin: 5,
  haloHead: 4,
  /** The coloured stroke itself; thicker while selected. */
  colour: 2.75,
  colourSelected: 4,
  rectRadius: 10,
} as const;

type Props = {
  item: AnnotationItem;
  imageWidth: number;
  imageHeight: number;
  scale: number;
  selected: boolean;
  onSelect: (id: string) => void;
  /** Focus lands on the group, so a mark is reachable and Enter-editable. */
  label: string;
  onRequestEdit: (id: string) => void;
};

/**
 * One mark, memoised.
 *
 * A freehand gesture fires `pointermove` at the pointer's own rate — every one of
 * those re-renders the canvas, and without this every already-finished mark
 * re-rendered too. At the hundred marks the limit allows, that is a hundred SVG
 * groups rebuilt per move event while the user is mid-stroke. The props are
 * primitives plus two stable callbacks, so a shallow compare is exactly right
 * here.
 */
export const MarkShape = memo(function MarkShape({
  item,
  imageWidth,
  imageHeight,
  scale,
  selected,
  onSelect,
  label,
  onRequestEdit,
}: Props) {
  const px = (value: number) => value * imageWidth;
  const py = (value: number) => value * imageHeight;
  const s = (value: number) => value * scale;

  const anchor = anchorOf(item.geometry);
  const badgeX = px(anchor.x);
  const badgeY = py(anchor.y);

  return (
    <g
      className={`mark mark-${item.palette}${selected ? " is-selected" : ""}`}
      role="button"
      tabIndex={0}
      aria-label={label}
      aria-pressed={selected}
      onPointerDown={(event) => {
        // Stopped so the canvas does not also treat this as a new-mark gesture
        // or a pan start on the same press.
        event.stopPropagation();
        onSelect(item.id);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          onSelect(item.id);
          onRequestEdit(item.id);
        }
      }}
    >
      <Geometry item={item} px={px} py={py} s={s} selected={selected} />

      {selected && (
        <circle
          className="mark-selection-ring"
          cx={badgeX}
          cy={badgeY}
          r={s(20.5)}
          fill="none"
          strokeWidth={s(5)}
          stroke={HALO}
        />
      )}
      {selected && (
        <circle
          className="mark-selection-ring-inner"
          cx={badgeX}
          cy={badgeY}
          r={s(20.5)}
          fill="none"
          strokeWidth={s(2.5)}
        />
      )}

      <circle cx={badgeX} cy={badgeY} r={s(16)} fill={HALO} />
      <circle className="mark-badge" cx={badgeX} cy={badgeY} r={s(13.5)} />
      <text
        className="mark-numeral"
        x={badgeX}
        y={badgeY}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={s(13)}
        fontWeight={700}
        fill={HALO}
      >
        {item.created_order}
      </text>
    </g>
  );
});

type GeometryProps = {
  item: AnnotationItem;
  px: (value: number) => number;
  py: (value: number) => number;
  s: (value: number) => number;
  selected: boolean;
};

function Geometry({ item, px, py, s, selected }: GeometryProps) {
  const geometry = item.geometry;

  if ("point" in geometry) {
    // The pin's tail: a downward triangle under the badge, so the mark points at
    // a specific place rather than merely covering it.
    const x = px(geometry.point.x);
    const y = py(geometry.point.y);
    const tail = `${x - s(6)},${y + s(11)} ${x + s(6)},${y + s(11)} ${x},${y + s(24)}`;
    return (
      <>
        <polygon
          points={tail}
          fill={HALO}
          stroke={HALO}
          strokeWidth={s(MARK_STROKE.haloPin)}
        />
        <polygon className="mark-fill" points={tail} />
      </>
    );
  }

  if ("start" in geometry) {
    const x1 = px(geometry.start.x);
    const y1 = py(geometry.start.y);
    const x2 = px(geometry.end.x);
    const y2 = py(geometry.end.y);
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const head = s(15);
    const spread = 0.42;
    const headPoints = [
      `${x2},${y2}`,
      `${x2 - head * Math.cos(angle - spread)},${y2 - head * Math.sin(angle - spread)}`,
      `${x2 - head * Math.cos(angle + spread)},${y2 - head * Math.sin(angle + spread)}`,
    ].join(" ");
    return (
      <>
        <line
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          stroke={HALO}
          strokeWidth={s(MARK_STROKE.haloLine)}
          strokeLinecap="round"
        />
        <line
          className="mark-stroke"
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          strokeWidth={s(MARK_STROKE.colour)}
          strokeLinecap="round"
        />
        {/* paint-order puts the white outline behind the fill in one element,
            rather than needing a second stacked polygon. */}
        <polygon
          className="mark-fill"
          points={headPoints}
          stroke={HALO}
          strokeWidth={s(MARK_STROKE.haloHead)}
          paintOrder="stroke"
        />
      </>
    );
  }

  if ("width" in geometry) {
    const x = px(geometry.x);
    const y = py(geometry.y);
    // Derived from the opposite corner rather than by scaling the normalised
    // width, so the rectangle's edges land on exactly the same pixels the
    // corner handles are drawn at.
    const width = px(geometry.x + geometry.width) - x;
    const height = py(geometry.y + geometry.height) - y;
    const radius = s(MARK_STROKE.rectRadius);
    const handle = s(10);
    const corners: Array<[number, number]> = [
      [x, y],
      [x + width, y],
      [x, y + height],
      [x + width, y + height],
    ];
    return (
      <>
        <rect
          x={x}
          y={y}
          width={width}
          height={height}
          rx={radius}
          fill="none"
          stroke={HALO}
          strokeWidth={s(MARK_STROKE.haloRect)}
        />
        <rect
          className="mark-stroke"
          x={x}
          y={y}
          width={width}
          height={height}
          rx={radius}
          fill="none"
          strokeWidth={s(selected ? MARK_STROKE.colourSelected : MARK_STROKE.colour)}
        />
        {selected &&
          corners.map(([cx, cy]) => (
            <rect
              key={`${cx}-${cy}`}
              className="mark-handle"
              x={cx - handle / 2}
              y={cy - handle / 2}
              width={handle}
              height={handle}
              stroke={HALO}
              strokeWidth={s(2)}
            />
          ))}
      </>
    );
  }

  if ("points" in geometry) {
    const path = geometry.points.map((point) => `${px(point.x)},${py(point.y)}`).join(" ");
    return (
      <>
        <polyline
          points={path}
          fill="none"
          stroke={HALO}
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
      </>
    );
  }

  return null;
}
