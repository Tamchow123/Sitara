"""Contract: every option visual the schema promises actually ships.

The questionnaire schema is the authority on which options exist and which of
them carry a ``visual_key``; this package owns that schema, so this is where the
contract is asserted. The frontend's
``apps/web/src/features/questionnaire/visuals/asset-integrity.json`` is a small,
flat, generated artifact (written by ``apps/web/scripts/build-questionnaire-images.py``)
listing one entry per shipped visual — reading it here keeps the coupling
pointed at a stable generated file rather than making the web package
hand-parse this app's Django fixture envelope.

Only non-colour visual keys are covered. Colour swatches are rendered from
project-authored hex values in the frontend manifest rather than from image
files, so they never appear in the integrity manifest.
"""

import json
import os
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_V4_PATH = _HERE.parent.parent / "fixtures" / "questionnaire_v4.json"

# Walk up for the repository root rather than counting parents: the backend test
# container mounts apps/api alone at /app, where a fixed index would run off the
# filesystem root. Absent web package -> skip (partial checkout). Present web
# package but missing manifest -> a real failure, asserted below, never a skip.
# CI checks out the whole repository, so this contract always runs there.
_WEB_PACKAGE = next(
    (parent / "apps" / "web" for parent in _HERE.parents if (parent / "apps" / "web").is_dir()),
    None,
)
_INTEGRITY_PATH = (
    None
    if _WEB_PACKAGE is None
    else _WEB_PACKAGE / "src" / "features" / "questionnaire" / "visuals" / "asset-integrity.json"
)


def _schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))[0]["fields"]["schema"]


def _non_colour_visual_keys(schema: dict) -> set[str]:
    return {
        option["visual_key"]
        for step in schema["steps"]
        for question in step["questions"]
        for option in question.get("options", [])
        if option.get("visual_key") and not option["visual_key"].startswith("colour_")
    }


def test_the_contract_below_is_not_silently_skipped_in_ci() -> None:
    """CI must actually run the contract, never skip past it.

    The checks below skip when ``apps/web`` is absent, which is right for the
    backend container (it mounts ``apps/api`` alone). That tolerance must not
    extend to CI: if a future workflow change introduced a sparse or partial
    checkout, those checks would quietly stop running and the pipeline would
    stay green. This asserts the invariant instead of trusting a comment.
    """
    if os.environ.get("CI", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("only meaningful inside CI")
    assert _WEB_PACKAGE is not None, (
        "apps/web is missing from the CI checkout, so the questionnaire "
        "visual-key contract would silently skip. CI must check out the whole "
        "repository."
    )


@pytest.mark.skipif(_WEB_PACKAGE is None, reason="apps/web is not present in this checkout")
class TestQuestionnaireVisualKeys:
    @pytest.fixture(scope="class")
    def manifest(self) -> dict:
        assert _INTEGRITY_PATH is not None and _INTEGRITY_PATH.is_file(), (
            f"apps/web is present but {_INTEGRITY_PATH} is missing; "
            "run apps/web/scripts/build-questionnaire-images.py"
        )
        return json.loads(_INTEGRITY_PATH.read_text(encoding="utf-8"))

    def test_v4_non_colour_visual_keys_match_the_shipped_manifest(self, manifest: dict) -> None:
        """Bidirectional: no option without a visual, no visual without an option.

        A gap means an option silently degrades to text in the UI; an orphan
        means an unused asset is being served.
        """
        assert _non_colour_visual_keys(_schema(_V4_PATH)) == set(manifest)

    def test_every_manifest_entry_is_a_safe_local_asset(self, manifest: dict) -> None:
        for key, entry in manifest.items():
            path = entry["path"]
            assert path.startswith("/questionnaire-visuals/"), key
            assert ".." not in path, key
            assert path.endswith(".webp"), key
            assert entry["alt"].strip(), key
            assert len(entry["sha256"]) == 64, key
            # Provenance stays inside the project's own source photography.
            assert not entry["source"].startswith(("/", "http")), key
            assert ".." not in entry["source"], key

    def test_gharara_and_sharara_stay_distinct_constructions(self, manifest: dict) -> None:
        """A gharara joins and flares at the knee; a sharara flares from the waist.

        These are different constructions (CLAUDE.md section 12) and must never
        be described interchangeably in the text a user reads.
        """
        for key, entry in manifest.items():
            if "gharara" in key:
                assert "knee" in entry["alt"].lower(), key
            if "sharara" in key:
                assert "waist" in entry["alt"].lower(), key
