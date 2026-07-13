"""Blind review artefacts: no model identity may leak anywhere."""

import csv
import json

import pytest

from conftest import tiny_png_bytes
from model_eval.contact_sheet import (
    PARTIAL_BANNER_TEXT,
    build_contact_sheet,
    candidate_codes,
    prepare_blind_items,
)
from model_eval.result_store import ResultStore
from model_eval.scoring import HARD_FAILURE_FIELDS, RUBRIC_FIELDS, build_scoring_sheet
from test_result_store import make_record


MODEL_KEYS = ["alpha-model", "beta-model"]
REPLICATE_IDS = {"alpha-model": "acme/alpha-model", "beta-model": "acme/beta-model"}


def leak_terms(store: ResultStore) -> list[str]:
    """Every string that must never appear in blind artefacts."""
    terms = list(MODEL_KEYS) + list(REPLICATE_IDS.values())
    for record in store.load_all():
        terms.append(record.request_id)
        if record.output_path:
            terms.append(record.output_path.split("/")[-1])
    return terms


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

    def test_blind_items_use_anonymised_names_and_copies(self, populated_store):
        items, blind_dir = prepare_blind_items(populated_store, "run-1")
        assert blind_dir == populated_store.run_dir / "blind"
        assert [i.blind_id for i in items] == ["item-001", "item-002"]
        for item in items:
            assert item.image_filename.startswith("image-")
            assert (blind_dir / item.image_filename).exists()
            for term in MODEL_KEYS:
                assert term not in item.image_filename
                assert term not in item.blind_id

    def test_blind_sheet_leaks_no_model_identity_anywhere(self, populated_store):
        sheet, mapping = build_contact_sheet(populated_store, "run-1")
        assert sheet.parent.name == "blind"
        html = sheet.read_text(encoding="utf-8")
        for term in leak_terms(populated_store):
            assert term not in html, f"blind HTML leaked {term!r}"
        assert "Candidate " in html
        assert "item-001" in html and "image-001" in html
        assert "candidate_key.json" in html  # the do-not-open warning

    def test_mapping_file_is_separate_and_holds_the_reverse_mapping(self, populated_store):
        _, mapping = build_contact_sheet(populated_store, "run-1")
        assert mapping.name == "candidate_key.json"
        data = json.loads(mapping.read_text(encoding="utf-8"))
        assert "do not open" in data["warning"].lower()
        for key in MODEL_KEYS:
            assert REPLICATE_IDS[key] in data["models"].values() or key in data["models"]
        items = data["items"]
        assert set(items) == {"item-001", "item-002"}
        for entry in items.values():
            assert entry["model_key"] in MODEL_KEYS
            assert entry["request_id"]
            assert entry["blind_image"].startswith("image-")

    def test_reveal_flag_shows_real_models_outside_the_blind_dir(self, populated_store):
        sheet, _ = build_contact_sheet(populated_store, "run-1", reveal=True)
        assert sheet.parent == populated_store.run_dir
        html = sheet.read_text(encoding="utf-8")
        for key in MODEL_KEYS:
            assert REPLICATE_IDS[key] in html

    def test_partial_banner_is_prominent_when_requested(self, populated_store):
        sheet, _ = build_contact_sheet(populated_store, "run-1", partial=True)
        html = sheet.read_text(encoding="utf-8")
        assert html.count(PARTIAL_BANNER_TEXT) >= 2  # top and bottom
        clean_sheet, _ = build_contact_sheet(populated_store, "run-1", partial=False)
        assert PARTIAL_BANNER_TEXT not in clean_sheet.read_text(encoding="utf-8")


class TestScoringSheet:
    def test_scoring_sheet_is_blind_and_uses_full_rubric(self, populated_store):
        path = build_scoring_sheet(populated_store, "run-1")
        assert path.parent.name == "blind"
        text = path.read_text(encoding="utf-8")
        for term in leak_terms(populated_store):
            assert term not in text, f"scoring CSV leaked {term!r}"
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        header, data = rows[0], rows[1:]
        assert "blind_item_id" in header
        assert "request_id" not in header
        for field in RUBRIC_FIELDS:
            assert f"{field} (1-5)" in header
        for field in HARD_FAILURE_FIELDS:
            assert f"{field} (yes/no)" in header
        assert "reviewer_notes" in header
        assert len(data) == len(MODEL_KEYS)
        flattened = "\n".join(",".join(row) for row in data)
        assert "item-001" in flattened and "image-001" in flattened
        assert "Candidate " in flattened

    def test_rubric_includes_bridal_distinctiveness_and_non_bridal_hard_failure(self):
        assert "bridal_occasion_distinctiveness" in RUBRIC_FIELDS
        assert "hf_reads_as_non_bridal_everydaywear" in HARD_FAILURE_FIELDS

    def test_partial_scoring_sheet_carries_the_warning_row(self, populated_store):
        path = build_scoring_sheet(populated_store, "run-1", partial=True)
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        assert PARTIAL_BANNER_TEXT in first_line
