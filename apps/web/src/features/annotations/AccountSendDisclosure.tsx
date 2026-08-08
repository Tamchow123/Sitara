// The one sentence §8.5 requires before a render is ever emailed.
//
// ADR 0021 records three exposures as ACCEPTED, not removed. The second of them —
// that a private concept image leaves Sitara for the configured SMTP provider and
// the recipient's own mail host, either of which may keep it indefinitely — has to
// be said in the UI, in plain words, before the first send. Explicitly "not buried
// in a policy page": the privacy page carries it too, but that page does not
// discharge this requirement.
//
// It lives in one component because there are TWO send surfaces — the concept
// result screen (plain render) and this workspace (annotated render) — and the
// disclosure was originally written into only the second. A user who never opens
// Annotate reaches the plain button with one click and would have seen nothing.
// Two copies of the sentence would also let a future edit leave the two surfaces
// describing the same accepted exposure differently, which is its own kind of
// dishonesty.

export function AccountSendDisclosure({ kind }: { kind: "plain" | "annotated" }) {
  return (
    <>
      Sending this emails the image
      {kind === "annotated" ? ", with your notes on it," : ""} to your account address, so it
      reaches your mail provider and your inbox — both of which may keep a copy outside
      Sitara&rsquo;s control.
    </>
  );
}
