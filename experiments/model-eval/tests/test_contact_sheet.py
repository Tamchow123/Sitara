"""Contact sheets and scoring templates: blind by default."""

import csv

import pytest

from conftest import tiny_png_bytes
from model_eval.contact_sheet import build_contact_sheet, candidate_codes
from model_eval.result_store import ResultStore
from model_eval.scoring import HARD_FAILURE_FIELDS, RUBRIC_FIELDS, build_scoring_sheet
from test_result_store import make_record


MODEL_KEYS = ["alpha-model", "beta-model"]
REPLICATE_IDS = {"alpha-model": "acme/alpha-model", "beta-model": "acme/beta-model"}


@pytest.fixture
def populated_store(tmp_path) -> ResultStore:
    store = ResultStore(tmp_path / "run")
    store.results_dir.mkdir(parents=True)
    store.images_dir.mkdir(parents=True)
    for i, key in enumerate(MODEL_KEYS):
        rid = f"screening--brief-a--{key}--editorial--text_only--s11--base"
        image = store.images_dir / f"{rid}.png"
        image.write_bytes(tiny_png_bytes((40 * i, 90, 120)))
        record = make_record(request_id=rid).model_copy(
            update={
                "model_key": key,
                "replicate_id": REPLICATE_IDS[key],
                "output_path": f"images/{rid}.png",
            }
        )
        store.save(record)
    return store


class TestAnonymisation:
    def test_codes_are_anonymous_and_deterministic_per_run(self):
        codes_a = candidate_codes("run-1", MODEL_KEYS)
        codes_b = candidate_codes("run-1", MODEL_KEYS)
        assert codes_a == codes_b
        for key, code in codes_a.items():
            assert key not in code
            assert code.startswith("Candidate ")

    def test_sheet_is_blind_by_default(self, populated_store):
        sheet, mapping = build_contact_sheet(populated_store, "run-1")
        html = sheet.read_text(encoding="utf-8")
        for key in MODEL_KEYS:
            assert REPLICATE_IDS[key] not in html
        assert "Candidate " in html
        assert "Do not open candidate_key.json" in html
        # The mapping lives in a separate artefact, not in the sheet.
        assert mapping.name == "candidate_key.json"
        mapping_text = mapping.read_text(encoding="utf-8")
        for key in MODEL_KEYS:
            assert REPLICATE_IDS[key] in mapping_text

    def test_reveal_flag_shows_real_models(self, populated_store):
        sheet, _ = build_contact_sheet(populated_store, "run-1", reveal=True)
        html = sheet.read_text(encoding="utf-8")
        for key in MODEL_KEYS:
            assert REPLICATE_IDS[key] in html


class TestScoringSheet:
    def test_scoring_sheet_uses_codes_and_full_rubric(self, populated_store):
        path = build_scoring_sheet(populated_store, "run-1")
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        header, data = rows[0], rows[1:]
        for field in RUBRIC_FIELDS:
            assert f"{field} (1-5)" in header
        for field in HARD_FAILURE_FIELDS:
            assert f"{field} (yes/no)" in header
        assert "reviewer_notes" in header
        assert len(data) == len(MODEL_KEYS)
        flattened = "\n".join(",".join(row) for row in data)
        for key in MODEL_KEYS:
            assert REPLICATE_IDS[key] not in flattened
        assert "Candidate " in flattened
