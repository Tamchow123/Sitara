"""Derive the questionnaire v5 fixture from v4.

v5 is v4 plus option DESCRIPTIONS, and nothing else. Same questions, same ids,
same options, same order, same rules, same constraints — so a design pinned to
v4 keeps its exact historical semantics and nothing about generation, the
DesignSpec or the image prompt changes.

Why a new version at all, for text a validator never reads: published versions
are immutable (ADR 0005, enforced in ``QuestionnaireVersion.save()`` and
reaffirmed by the Phase 9 follow-up that restored ``questionnaire_v1.json``
byte-for-byte after it was edited in place). An option description is
presentation metadata, but it lives inside the frozen ``schema`` JSON, so the
rule applies whole. v4 is left untouched, whatever its status is in any
environment.

What this fixes (Phase 23 Part C): ``ChoiceOptionCard`` mounts its info trigger
only for an option that carries a description, so a question where one option
has one and eleven do not renders a single unexplained "i". That was the
reported defect on ``fabrics`` — satin arrived in Phase 16B (ADR 0018) with a
description, alongside eleven options inherited from v1 that never had one.

Three questions are filled, and one is deliberately left alone:

- ``fabrics`` (11 missing) and ``embellishment_styles`` (13 missing) are the two
  most culturally specific vocabularies in the product. The difference between
  zardozi, dabka, nakshi, gota patti and chikankari is not decoration — it is
  regional craft tradition, and a customer choosing between them without being
  told what they are is exactly the flattening CLAUDE.md §12 forbids.
- ``embellishment_density`` (3 missing) is three short lines; the labels almost
  explain themselves, but three of three is cheap and consistency is the ask.
- ``regional_style`` stays at ZERO of nine. See below.

Run:  python apps/api/sitara/questionnaire/fixtures/build_v5.py
It is deterministic: re-running produces a byte-identical file.
"""

import json
import pathlib

HERE = pathlib.Path(__file__).parent
V4 = HERE / "questionnaire_v4.json"
V5 = HERE / "questionnaire_v5.json"

# A fixed UUID so re-running this script (or reloading the fixture) updates the
# same row instead of creating a second one.
V5_PK = "9c4a7d21-3e6b-4f58-8a0d-7b2e5c91f4a6"

# ---------------------------------------------------------------------------
# ``regional_style`` IS DELIBERATELY NOT DESCRIBED. Do not "finish" it.
# ---------------------------------------------------------------------------
# Zero of nine is an internally consistent "none" for that question, and it is
# the project owner's decision (Phase 23, decision 4). This was never a
# copywriting task: CLAUDE.md §12 requires regional influences to stay OPTIONAL
# AND NON-PRESCRIPTIVE, and a sentence explaining what "Rajasthani bridal
# influences" means can very easily become prescriptive, or wrong, or both —
# nine short sentences would be nine claims about how nine communities dress.
#
# A future contributor filling these in "for tidiness" would ship those nine
# claims without anyone having reviewed them. If they are ever to be written,
# they need the owner's cultural review first, as their own piece of work.
#
# The same restraint applies to the craft descriptions below: each describes
# what the technique LOOKS LIKE and how it is worked, never which community or
# region it belongs to. Naming provenance is a separate, reviewable claim.
REGIONAL_STYLE_STAYS_UNDESCRIBED = "regional_style"

# ``satin`` already carries the description ADR 0018 shipped with it; it is
# repeated here verbatim so this table is the whole answer for the question
# rather than a patch over part of it, and a test asserts it is unchanged.
FABRIC_DESCRIPTIONS = {
    "silk": "A fine woven cloth with a soft sheen and a fluid fall.",
    "raw_silk": "Silk left with its natural slubs - a matt texture with body and structure.",
    "satin": "A smooth fabric with a lustrous face that catches light cleanly.",
    "velvet": "A dense pile that reads deep and rich, and hangs with real weight.",
    "organza": "Crisp and sheer - it holds its shape and adds volume without weight.",
    "chiffon": "Light, sheer and softly draping, with a faint grainy texture.",
    "georgette": "A little heavier and more textured than chiffon, with a gentle ripple.",
    "net": "An open mesh, worn sheer or layered to add lift.",
    "brocade": "Pattern woven into the cloth itself, often in metallic thread.",
    "jamawar": "A woven shawl-cloth tradition of intricate all-over patterning.",
    "tissue": "A very fine cloth shot through with metallic thread, so it shimmers.",
    "cotton_silk": "A cotton and silk blend - breathable and matt, with a light sheen.",
}

EMBELLISHMENT_DESCRIPTIONS = {
    "zardozi": "Raised metallic thread embroidery, worked over a padded base so it stands proud.",
    "dabka": "Fine coiled metallic wire couched to the cloth in a smooth, continuous line.",
    "nakshi": "A crimped metallic wire whose facets catch light more sharply than a smooth coil.",
    "gota_patti": "Flat metallic ribbon cut into shapes and stitched down in bold outlines.",
    "mirror_work": "Small mirrors held to the cloth by an embroidered edge around each one.",
    "resham_threadwork": "Coloured silk thread worked flat against the cloth rather than raised.",
    "chikankari": "Fine white-on-white shadow work - subtle, textural and understated.",
    "sequins": "Small flat discs stitched close together for an overall shimmer.",
    "pearls": "Rounded pearl beads, placed sparingly or massed for a soft lustre.",
    "crystals": "Cut stones that throw sharp points of light.",
    "beads": "Glass or metal beads stitched in patterns, adding texture and a little weight.",
    "applique": "Shapes cut from one cloth and stitched onto another.",
    "none": "No surface work at all - the cloth, its colour and its fall carry the design.",
}

DENSITY_DESCRIPTIONS = {
    "minimal": "Light, placed work with plenty of plain cloth left showing.",
    "balanced": "Worked in places and plain in others - detail without weight.",
    "heavy": "Dense work over much of the surface, with real weight to it.",
}

DESCRIPTIONS = {
    "fabrics": FABRIC_DESCRIPTIONS,
    "embellishment_styles": EMBELLISHMENT_DESCRIPTIONS,
    "embellishment_density": DENSITY_DESCRIPTIONS,
}


def build() -> dict:
    v4 = json.loads(V4.read_text(encoding="utf-8"))
    record = v4[0]
    schema = record["fields"]["schema"]

    for step in schema["steps"]:
        for question in step["questions"]:
            descriptions = DESCRIPTIONS.get(question["id"])
            if descriptions is None:
                continue
            declared = {option["value"] for option in question["options"]}
            # Fail loudly rather than silently describing seven of twelve: if v4
            # ever gains or loses an option, this script must be updated with it.
            if declared != set(descriptions):
                raise SystemExit(
                    f"{question['id']}: description table does not match the "
                    f"v4 options exactly (missing "
                    f"{sorted(declared - set(descriptions))}, extra "
                    f"{sorted(set(descriptions) - declared)})"
                )
            for option in question["options"]:
                option["description"] = descriptions[option["value"]]

    return {
        "model": record["model"],
        "pk": V5_PK,
        "fields": {
            **record["fields"],
            "version": 5,
            # A DRAFT. Activation is an operator step through
            # ``activate_questionnaire_version``; ``loaddata`` never activates
            # anything, and must not start here.
            "status": "draft",
            "schema": schema,
        },
    }


def render() -> str:
    """The exact text ``questionnaire_v5.json`` should contain.

    Kept separate from writing the file so a test can assert the committed
    fixture still equals a fresh render — the same freshness guard v4, the
    OpenAPI schema and the DesignSpec schema export already have."""
    return json.dumps([build()], indent=2, ensure_ascii=False) + "\n"


def write() -> None:
    V5.write_text(render(), encoding="utf-8")
    print("wrote", V5.name)


if __name__ == "__main__":
    write()
