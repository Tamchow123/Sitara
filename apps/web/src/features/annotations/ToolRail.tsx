"use client";

// The tool rail: a real `role="toolbar"` with a roving tabindex, so the whole
// rail is one Tab stop and the arrow keys move between tools. Tabbing through
// six buttons to reach the annotation list would make the keyboard path far
// worse than the pointer one.
//
// `Note` is NOT a fifth geometry type. The schema has exactly four, and a note
// without a mark has nowhere to point — this is a shortcut to the note editor of
// the selected (or most recent) mark, which is what the handoff's rail implies
// and what keeps the schema closed.

import { useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import type { Tool } from "./AnnotationCanvas";
import { PinGlyph } from "./Glyphs";

type ToolSpec = {
  id: Tool | "note";
  label: string;
  shortcut: string;
  glyph: React.ReactNode;
};

const TOOLS: ToolSpec[] = [
  { id: "select", label: "Select or pan", shortcut: "V", glyph: <CursorGlyph /> },
  { id: "pin", label: "Pin", shortcut: "P", glyph: <PinGlyph /> },
  { id: "arrow", label: "Arrow", shortcut: "A", glyph: <ArrowGlyph /> },
  { id: "rectangle", label: "Rectangle", shortcut: "R", glyph: <RectGlyph /> },
  { id: "freehand", label: "Freehand", shortcut: "F", glyph: <ScribbleGlyph /> },
  { id: "note", label: "Edit note", shortcut: "N", glyph: <NoteGlyph /> },
];

type Props = {
  tool: Tool;
  onToolChange: (tool: Tool) => void;
  onFocusNoteEditor: () => void;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
};

export function ToolRail({
  tool,
  onToolChange,
  onFocusNoteEditor,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
}: Props) {
  const railRef = useRef<HTMLDivElement>(null);
  // userAgent rather than the deprecated navigator.platform. Only affects the
  // label text, so a wrong guess costs a slightly odd shortcut hint, never
  // behaviour — both modifiers are accepted by the key handler regardless.
  const isMac =
    typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/i.test(navigator.userAgent);
  const modifier = isMac ? "⌘" : "Ctrl";

  function activate(spec: ToolSpec) {
    if (spec.id === "note") onFocusNoteEditor();
    else onToolChange(spec.id as Tool);
  }

  // Roving tabindex: exactly one button is tabbable, and Arrow/Home/End move
  // focus within the rail.
  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const keys = ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"];
    if (!keys.includes(event.key)) return;
    const buttons = Array.from(
      railRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [],
    );
    if (buttons.length === 0) return;
    const index = buttons.findIndex((button) => button === document.activeElement);
    event.preventDefault();
    if (event.key === "Home") {
      buttons[0]!.focus();
      return;
    }
    if (event.key === "End") {
      buttons[buttons.length - 1]!.focus();
      return;
    }
    const forward = event.key === "ArrowDown" || event.key === "ArrowRight";
    const next = (index + (forward ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]!.focus();
  }

  return (
    <div
      className="annotation-rail"
      role="toolbar"
      aria-label="Annotation tools"
      aria-orientation="vertical"
      ref={railRef}
      onKeyDown={onKeyDown}
    >
      {TOOLS.map((spec) => {
        const active = spec.id === tool;
        return (
          <button
            key={spec.id}
            type="button"
            className={`annotation-tool${active ? " is-active" : ""}`}
            aria-label={`${spec.label} (${spec.shortcut})`}
            aria-pressed={spec.id === "note" ? undefined : active}
            title={`${spec.label} (${spec.shortcut})`}
            tabIndex={active ? 0 : -1}
            onClick={() => activate(spec)}
          >
            <span aria-hidden="true">{spec.glyph}</span>
          </button>
        );
      })}

      <span className="annotation-rail-divider" aria-hidden="true" />

      <button
        type="button"
        className="annotation-tool"
        aria-label={`Undo (${modifier}+Z)`}
        title={`Undo (${modifier}+Z)`}
        tabIndex={-1}
        disabled={!canUndo}
        onClick={onUndo}
      >
        <span aria-hidden="true">
          <UndoGlyph />
        </span>
      </button>
      <button
        type="button"
        className="annotation-tool"
        aria-label={`Redo (${modifier}+Shift+Z)`}
        title={`Redo (${modifier}+Shift+Z)`}
        tabIndex={-1}
        disabled={!canRedo}
        onClick={onRedo}
      >
        <span aria-hidden="true">
          <RedoGlyph />
        </span>
      </button>
    </div>
  );
}

// Inline glyphs rather than an icon dependency: six shapes do not justify a
// package, and CLAUDE.md rules out new frontend dependencies without a phase need.

function CursorGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18">
      <path d="M5 3l14 8-6 1.5L10 20 5 3z" fill="currentColor" />
    </svg>
  );
}
function ArrowGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18">
      <path
        d="M4 20L19 5m0 0h-7m7 0v7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function RectGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18">
      <rect
        x="4"
        y="6"
        width="16"
        height="12"
        rx="2.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
      />
    </svg>
  );
}
function ScribbleGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18">
      <path
        d="M3 15c3-6 5 3 8-1s4 4 7-2"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
function NoteGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18">
      <path
        d="M4 5h16M4 11h16M4 17h9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
function UndoGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18">
      <path
        d="M9 7H5V3m0 4a8 8 0 1 1-1 9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function RedoGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18">
      <path
        d="M15 7h4V3m0 4a8 8 0 1 0 1 9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
