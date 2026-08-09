/**
 * The name this platform gives the undo modifier.
 *
 * Label text only. The key handler accepts BOTH `metaKey` and `ctrlKey` whatever
 * this returns, so a wrong guess costs a slightly odd hint and never behaviour.
 *
 * Shared rather than copied: the tool rail's undo and redo tooltips and the
 * gesture-and-shortcut help both name this key, and two independent copies of a
 * platform sniff would eventually disagree — one saying ⌘ while the other said
 * Ctrl on the same machine, which reads as a bug in the app rather than in the
 * detection.
 *
 * `userAgent` rather than the deprecated `navigator.platform`. Guarded for the
 * server, where there is no navigator at all and this module is still imported
 * during rendering.
 */
export function undoModifierLabel(): string {
  if (typeof navigator === "undefined") return "Ctrl";
  return /Mac|iPhone|iPad|iPod/i.test(navigator.userAgent) ? "⌘" : "Ctrl";
}
