"""Provenance records: required fields, durability, overwrite protection."""

import hashlib

import pytest

from model_eval.result_store import (
    REQUIRED_PROVENANCE_FIELDS,
    ResultRecord,
    ResultStore,
    ResultStoreError,
    sha256_of,
)


def make_record(request_id="screening--b--m--editorial--text_only--s11--base", status="succeeded"):
    return ResultRecord(
        run_id="run-1",
        stage="screening",
        request_id=request_id,
        brief_id="b",
        garment="lehenga",
        ceremony="baraat",
        tags=["screening"],
        model_key="m",
        replicate_id="owner/m",
        model_version="v-abc",
        provider_prediction_id="pred-1",
        prompt_format="editorial",
        prompt_text="a prompt",
        negative_text=None,
        json_payload=None,
        inspiration_mode="text_only",
        reference_ids=[],
        kind="base",
        refinement_id=None,
        refinement_strategy=None,
        base_request_id=None,
        seed=11,
        input_params={"prompt": "a prompt", "seed": 11, "aspect_ratio": "3:4"},
        aspect_ratio="3:4",
        width=768,
        height=1024,
        started_at="2026-07-13T00:00:00+00:00",
        completed_at="2026-07-13T00:00:09+00:00",
        latency_seconds=9.0,
        status=status,
        error_category=None,
        error_message=None,
        estimated_max_cost_usd=0.1,
        reconciled_cost_usd=0.04,
        cost_basis="calculated",
        output_path="images/out.png",
        output_mime_type="image/png",
        output_sha256="abc123",
        pricing_checked_on="2026-07-13",
        git_commit="deadbeef",
    )


class TestProvenance:
    def test_record_schema_covers_every_required_provenance_field(self):
        assert set(ResultRecord.model_fields.keys()) == set(REQUIRED_PROVENANCE_FIELDS)

    def test_round_trip(self, tmp_path):
        store = ResultStore(tmp_path)
        record = make_record()
        store.save(record)
        assert store.load(record.request_id) == record
        assert store.load_all() == [record]


class TestOverwriteProtection:
    def test_existing_record_is_never_silently_overwritten(self, tmp_path):
        store = ResultStore(tmp_path)
        record = make_record()
        store.save(record)
        with pytest.raises(ResultStoreError, match="refusing to overwrite"):
            store.save(record)
        # Even with the retry flag, a succeeded record stays protected:
        with pytest.raises(ResultStoreError, match="refusing to overwrite"):
            store.save(record, allow_replace_failed=True)

    def test_failed_record_replaceable_only_explicitly(self, tmp_path):
        store = ResultStore(tmp_path)
        failed = make_record(status="failed")
        store.save(failed)
        retried = make_record(status="succeeded")
        with pytest.raises(ResultStoreError):
            store.save(retried)  # implicit replacement refused
        store.save(retried, allow_replace_failed=True)
        assert store.load(retried.request_id).status == "succeeded"


class TestHashing:
    def test_sha256_of_matches_hashlib(self, tmp_path):
        payload = b"sitara-test-bytes"
        f = tmp_path / "img.png"
        f.write_bytes(payload)
        assert sha256_of(f) == hashlib.sha256(payload).hexdigest()


class TestAttemptJournal:
    def test_attempt_round_trip_and_clear(self, tmp_path):
        store = ResultStore(tmp_path)
        assert store.load_attempt("req-1") is None
        store.save_attempt("req-1", {"request_id": "req-1", "state": "reserved"})
        assert store.load_attempt("req-1") == {"request_id": "req-1", "state": "reserved"}
        # State transitions overwrite atomically:
        store.save_attempt(
            "req-1",
            {"request_id": "req-1", "state": "submitted", "prediction_id": "pred-9"},
        )
        assert store.load_attempt("req-1")["prediction_id"] == "pred-9"
        store.clear_attempt("req-1")
        assert store.load_attempt("req-1") is None
        store.clear_attempt("req-1")  # idempotent
