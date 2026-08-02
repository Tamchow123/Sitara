// Concept visualisation and its limits (Phase 17 §16). Static server
// component — nothing here is fetched or personalised.
//
// The job of this page is to be the honest version of what the result screen
// says in one sentence. It must not promise constructibility, must not present
// refinement as image editing or as unlimited, and must not imply that a
// regional direction speaks for any community.

import type { Metadata } from "next";
import Link from "next/link";

import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "About Sitara concepts — Sitara",
  description:
    "What an AI-assisted bridalwear concept is, what it is not, and how far a tailor can take it.",
};

export default function ConceptsPage() {
  return (
    <AppShell width="narrow">
      <p className="kicker">Concept visualisation</p>
      <h1>What Sitara makes, and what it does not</h1>
      <p className="lede">
        Sitara turns your answers into a written design brief and one AI-assisted visual concept. It
        is a starting point for a conversation with a tailor — not a garment, and not instructions
        for making one.
      </p>

      <section className="panel" aria-labelledby="concepts-not">
        <h2 id="concepts-not">A concept is not a pattern</h2>
        <p>
          The image is a visualisation, not a photograph of a finished outfit. Sitara does not
          produce sewing patterns, cutting layouts, measurements, yardages or construction
          specifications, and it does not guarantee that a garment can be built exactly as shown.
        </p>
        <p>
          Some of what an image shows may be difficult, expensive or impossible to realise in cloth.
          Colours, fabric behaviour, embroidery density and fine detail all change when interpreted
          physically. Your tailor&apos;s judgement, not the image, decides what is achievable.
        </p>
        <p>
          Take the written brief with you as well as the picture. It is the part a tailor can
          actually work from, and it says in words what the image only implies.
        </p>
      </section>

      <section className="panel" aria-labelledby="concepts-refine">
        <h2 id="concepts-refine">One refinement, and what it changes</h2>
        <p>
          Each design may be refined once. You choose one area to change — colour, fabric,
          embellishment, coverage or drape — and Sitara applies your request within that area only.
        </p>
        <p>
          Refinement generates a completely new image. It is not an edit of the first one: your
          original image is never sent anywhere or altered, and the refined concept may differ from
          it in pose, framing, face and the placement of embroidery, even where you asked for no
          change. Sitara keeps as much continuity as it can, but continuity is an aim, not a
          promise.
        </p>
        <p>
          Your original concept is kept. After a refinement you can see both versions side by side.
        </p>
        <p>
          If neither version is right, editing your answers and generating again is the way forward
          — that starts a new concept rather than a second refinement.
        </p>
      </section>

      <section className="panel" aria-labelledby="concepts-culture">
        <h2 id="concepts-culture">Cultural direction, not cultural authority</h2>
        <p>
          South Asian bridalwear is not one tradition. A gharara is not a sharara; a draped saree is
          not a lehenga; ceremonies differ between communities and between families within a
          community.
        </p>
        <p>
          Where you name a regional influence, Sitara treats it as a direction for the design, never
          as a statement about how any community dresses. Nothing Sitara produces should be read as
          the correct or complete form of anyone&apos;s custom.
        </p>
      </section>

      <section className="panel" aria-labelledby="concepts-inspiration">
        <h2 id="concepts-inspiration">Inspiration references</h2>
        <p>
          References you choose guide the concept. The result is an interpretation, not a
          reproduction of any of them, and Sitara does not attempt to copy a specific garment or
          designer.
        </p>
        <p>
          Designer and brand names are not part of what you can ask for.{" "}
          <Link href="/privacy">What happens to a reference image you select</Link> is set out on the
          privacy page.
        </p>
      </section>
    </AppShell>
  );
}
