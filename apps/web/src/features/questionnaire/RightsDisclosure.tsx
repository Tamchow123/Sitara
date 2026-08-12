// The ADR 0019 disclosure, in one place (Phase 22, ADR 0026).
//
// It was written for two screens — the shop's iPad and the customer's own
// phone. Phase 22 requires the phone to show "the full ADR 0019 disclosure ...
// in the same words as the iPad, not a shortened version. A phone screen is
// smaller; that is a layout problem, not a licence to abbreviate."
//
// Two copies of that paragraph would eventually differ, and the one that got
// shortened would be the phone's, because it is the one that does not fit. So
// there is one copy. Layout differences belong in CSS.
//
// Since ADR 0026's amendment (2026-08-12) only the phone renders it: the iPad's
// camera and file picker are gone, and its affirmation went with the controls it
// gated. `scope="own-device"` therefore has no caller today. It is kept, with
// its test, because it is one sentence and it is the wording any future
// device-local upload path must reuse rather than reinvent — not because
// anything still shows it.
//
// The substance is not ours to soften: BFL's terms take a perpetual,
// irrevocable licence over inputs; Replicate publishes no input retention
// window; whether those terms differ for Replicate-routed traffic is
// unresolved. ADR 0019's exposure is ACCEPTED and disclosed, not removed, and
// this text is the disclosure. Editing it to sound better is editing what
// someone is consenting to.

export const RIGHTS_AFFIRMATION_LABEL =
  "I have the right to use these images, and I understand they will be sent " +
  "to the AI image provider on the terms above.";

type Props = {
  /** Wired to the affirmation checkbox's `aria-describedby`. */
  id: string;
  /** The privacy sentence differs: the iPad says "your design", the phone is
   *  talking to someone who has not seen the design and did not start it. */
  scope: "own-device" | "handoff";
};

export function RightsDisclosure({ id, scope }: Props) {
  return (
    <div className="upload-disclosure" id={id}>
      <p>
        {scope === "handoff"
          ? "These go straight to the design being put together for you on the shop's screen. They are private to it, are never added to Sitara's catalogue, are never shown to anyone else, and are deleted with the design."
          : "These are private to your design. They are never added to Sitara's catalogue, never shown to anyone else, and are deleted with your design."}
      </p>
      <p>
        <strong>Before you add a photograph, please read this.</strong> If you
        use an image as a reference, its file is sent to the external AI image
        provider that draws your concept. That provider&apos;s terms take a
        perpetual, irrevocable licence over what it receives, to train and
        improve their technology. They publish no time limit on how long they
        keep it, and it is unresolved whether those terms differ when Sitara
        reaches them through Replicate. Sitara cannot undo that once an image is
        sent. Please only add an image you are comfortable handing over on those
        terms — and not one that shows someone who has not agreed to it.
      </p>
    </div>
  );
}
