"""The account gallery's list payload (Phase 21, ADR 0024).

The gallery is the first screen that shows someone several of their own concepts
at once, so the interesting tests here are not "does it list things" but the three
ways a list can betray a private design: by carrying a bearer token, by carrying
someone else's row, or by carrying provenance the detail endpoints are careful to
withhold.
"""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from sitara.designs.models import (
    DESIGN_TITLE_MAX_LENGTH,
    Design,
    DesignSession,
    DesignVersion,
    GenerationAttempt,
)
from sitara.designs.serializers import (
    GALLERY_OFFSET_MAX,
    GALLERY_PAGE_SIZE_DEFAULT,
    GALLERY_PAGE_SIZE_MAX,
    UNTITLED_CONCEPT_DISPLAY_TITLE,
)
from sitara.designs.services import DESIGN_SESSION_KEY

from .utils import (
    DESIGNS_URL,
    bootstrap_csrf,
    create_design,
    create_owned_design_id,
    create_pending_design_version,
    create_ready_design_version,
    csrf_client,
    signed_in_client,
    staged_image_provenance,
    unique_email,
)

pytestmark = pytest.mark.django_db

GALLERY_ROW_KEYS = {
    "id",
    "title",
    "display_title",
    "status",
    "created_at",
    "updated_at",
    "is_demo",
    "version_count",
    "versions",
}

GALLERY_VERSION_KEYS = {
    "id",
    "version_number",
    "is_demo",
    "has_image",
    "job_status",
    "created_at",
}


def gallery(client: Client, query: str = ""):
    return client.get(f"{DESIGNS_URL}{query}")


def attempt_for(version, *, status: str = "succeeded"):
    """The generation attempt that produced a version, as the pipeline links it.

    A terminal attempt is not just a row with a status: the model's check
    constraints require a succeeded one to carry a staging key, an empty error
    code and a completion time, and a failed one to carry a non-empty stable error
    code and a completion time. Building those here rather than per test is what
    stops a fixture from drifting into a shape the pipeline never produces.

    Deliberately never `is_demo=True`: a succeeded demo attempt additionally
    requires its selection provenance, and the gallery reads the demo flag from
    ``DesignVersion.is_demo`` rather than from the attempt anyway — so a demo knob
    here would only be a way to build an invalid row."""
    terminal = {
        "succeeded": {
            **staged_image_provenance(),
            "error_code": "",
            "completed_at": timezone.now(),
        },
        "failed": {
            "error_code": "internal_generation_error",
            "completed_at": timezone.now(),
        },
    }.get(status, {})
    return GenerationAttempt.objects.create(
        design_id=version.design_id,
        design_version=version,
        status=status,
        **terminal,
    )


class TestShape:
    def test_a_row_carries_exactly_the_gallery_fields(self):
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser, title="Autumn walima")
        version = create_ready_design_version(design_id, with_storage_objects=False)
        attempt_for(version)

        response = gallery(browser)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        body = response.json()
        assert set(body) == {"designs", "total", "limit", "offset"}
        (row,) = body["designs"]
        assert set(row) == GALLERY_ROW_KEYS
        assert set(row["versions"][0]) == GALLERY_VERSION_KEYS

    def test_a_named_design_shows_the_name_its_owner_gave_it(self):
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser, title="Autumn walima")
        create_ready_design_version(
            design_id,
            design_spec={"schema_version": 1, "title": "A spec-derived name"},
            with_storage_objects=False,
        )
        (row,) = gallery(browser).json()["designs"]
        # An explicit title wins: someone who named their design is not
        # overruled by the model's idea of what to call it.
        assert row["display_title"] == "Autumn walima"

    def test_an_unnamed_design_shows_the_concept_name_rather_than_an_empty_heading(self):
        """The case that is NOT hypothetical: the questionnaire never sets
        `Design.title`, so every concept made through the real product reaches the
        gallery with it blank. Without this the card's heading renders empty."""
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser, title="")
        create_ready_design_version(
            design_id,
            design_spec={"schema_version": 1, "title": "Ivory and rose lehenga"},
            with_storage_objects=False,
        )
        (row,) = gallery(browser).json()["designs"]
        assert row["title"] == ""
        assert row["display_title"] == "Ivory and rose lehenga"

    def test_a_renamed_refinement_is_what_the_card_shows(self):
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser, title="")
        first = create_ready_design_version(
            design_id,
            design_spec={"schema_version": 1, "title": "Ivory lehenga"},
            with_storage_objects=False,
        )
        create_ready_design_version(
            design_id,
            version_number=2,
            parent_version=first,
            design_spec={"schema_version": 1, "title": "Ivory lehenga, deeper red"},
            with_storage_objects=False,
        )
        (row,) = gallery(browser).json()["designs"]
        assert row["display_title"] == "Ivory lehenga, deeper red"

    def test_a_design_with_nothing_to_go_on_says_so_plainly(self):
        browser, _ = signed_in_client()
        create_owned_design_id(browser, title="")
        (row,) = gallery(browser).json()["designs"]
        assert row["display_title"] == UNTITLED_CONCEPT_DISPLAY_TITLE

    @pytest.mark.parametrize(
        "spec",
        [
            "not a mapping at all",
            [],
            {"schema_version": 1},
            {"schema_version": 1, "title": ""},
            {"schema_version": 1, "title": "   "},
            {"schema_version": 1, "title": 12345},
            {"schema_version": 1, "title": None},
            {"schema_version": 1, "title": {"nested": "object"}},
        ],
    )
    def test_a_spec_that_cannot_supply_a_name_falls_back_instead_of_raising(self, spec):
        """`design_spec` is stored JSON, not a validated field, so a row written by
        an older version of the pipeline or by a direct ORM write can hold anything.
        Total over arbitrary JSON, exactly as the questionnaire schema is: a
        controlled fallback, never a TypeError rendered as a 500.

        The completely spec-LESS row is a separate test below, because two check
        constraints make it unreachable in this shape: a prompt requires a spec, and
        a permanent image requires both."""
        browser, _ = signed_in_client()
        design_id = create_owned_design_id(browser, title="")
        version = create_ready_design_version(design_id, with_storage_objects=False)
        DesignVersion.objects.filter(pk=version.pk).update(design_spec=spec)

        response = gallery(browser)
        assert response.status_code == 200
        (row,) = response.json()["designs"]
        assert row["display_title"] == UNTITLED_CONCEPT_DISPLAY_TITLE

    def test_a_version_with_no_spec_at_all_falls_back(self):
        """A legacy pre-DesignSpec row, built the only way the database permits one:
        `designs_designversion_image_prompt_requires_spec` and
        `designs_designversion_permanent_image_requires_spec_prompt` together mean a
        spec-less version has no prompt and no stored image either."""
        browser, _ = signed_in_client()
        design_id = create_owned_design_id(browser, title="")
        create_pending_design_version(design_id)

        response = gallery(browser)
        assert response.status_code == 200
        (row,) = response.json()["designs"]
        assert row["display_title"] == UNTITLED_CONCEPT_DISPLAY_TITLE

    def test_an_over_long_concept_name_is_truncated_rather_than_returned_whole(self):
        browser, _ = signed_in_client()
        design_id = create_owned_design_id(browser, title="")
        create_ready_design_version(
            design_id,
            design_spec={"schema_version": 1, "title": "R" * (DESIGN_TITLE_MAX_LENGTH + 500)},
            with_storage_objects=False,
        )
        (row,) = gallery(browser).json()["designs"]
        assert len(row["display_title"]) == DESIGN_TITLE_MAX_LENGTH

    def test_an_empty_gallery_still_reports_its_paging(self):
        browser, _ = signed_in_client()
        body = gallery(browser).json()
        assert body["designs"] == []
        assert body["total"] == 0
        assert body["limit"] == GALLERY_PAGE_SIZE_DEFAULT
        assert body["offset"] == 0

    def test_listing_still_never_creates_a_workspace(self):
        # Unchanged from Phase 4 and re-proved here because the gallery is the one
        # page a signed-in visitor lands on before doing anything at all: if a
        # list request created a workspace, every visit would leave a row behind.
        browser = csrf_client()
        gallery(browser)
        gallery(browser, "?limit=5")
        assert DesignSession.objects.count() == 0
        assert DESIGN_SESSION_KEY not in browser.session


class TestVersionGrouping:
    def test_versions_come_back_in_creation_order_inside_their_design(self):
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser)
        first = create_ready_design_version(design_id, version_number=1, with_storage_objects=False)
        second = create_ready_design_version(
            design_id, version_number=2, with_storage_objects=False, parent_version=first
        )
        attempt_for(first)
        attempt_for(second)

        (row,) = gallery(browser).json()["designs"]
        assert [v["version_number"] for v in row["versions"]] == [1, 2]
        assert [v["id"] for v in row["versions"]] == [str(first.id), str(second.id)]
        assert row["version_count"] == 2

    def test_a_version_whose_image_has_not_arrived_says_so_rather_than_being_omitted(self):
        """The state the gallery must label instead of drawing a broken image.

        A DesignVersion is created before the permanent image ingest finishes, so
        this row is legitimate and reachable — not a corrupt fixture."""
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser)
        pending = create_pending_design_version(design_id)
        attempt_for(pending, status="running_image")

        (row,) = gallery(browser).json()["designs"]
        (version,) = row["versions"]
        assert version["has_image"] is False
        assert version["job_status"] == "running_image"

    def test_a_failed_generation_appears_with_its_state(self):
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser)
        Design.objects.filter(pk=design_id).update(status="generation_failed")
        failed = create_pending_design_version(design_id)
        attempt_for(failed, status="failed")

        (row,) = gallery(browser).json()["designs"]
        assert row["status"] == "generation_failed"
        assert row["versions"][0]["job_status"] == "failed"
        assert row["versions"][0]["has_image"] is False

    def test_a_draft_that_has_never_generated_reports_no_mode_rather_than_live(self):
        """`is_demo: null`, not `false`. Nothing has been generated, so there is no
        mode to report, and reporting `false` would label every untouched draft as
        a live concept."""
        browser, token = signed_in_client()
        create_owned_design_id(browser)

        (row,) = gallery(browser).json()["designs"]
        assert row["is_demo"] is None
        assert row["version_count"] == 0
        assert row["versions"] == []

    def test_the_design_reports_its_newest_version_s_mode(self):
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser)
        first = create_ready_design_version(design_id, version_number=1, with_storage_objects=False)
        newest = create_ready_design_version(
            design_id, version_number=2, with_storage_objects=False, parent_version=first
        )
        newest.is_demo = True
        newest.save(update_fields=["is_demo"])

        (row,) = gallery(browser).json()["designs"]
        assert row["is_demo"] is True


class TestPrivacy:
    def test_no_signed_url_storage_key_or_hash_appears_anywhere(self):
        """The whole reason the gallery mints its own URLs per card.

        A signed URL is a short-lived bearer token (CLAUDE.md §14). One in a list
        payload would be a token per concept, in a response that some client will
        eventually keep."""
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser)
        version = create_ready_design_version(design_id)
        attempt_for(version)

        raw = gallery(browser).content.decode()
        for forbidden in (
            "design-images/",
            "X-Amz",
            "Signature",
            "minio",
            "9000",
            version.image_storage_key,
            version.thumbnail_storage_key,
            version.image_sha256,
            version.thumbnail_sha256,
        ):
            assert forbidden not in raw, forbidden

    def test_no_prompt_design_spec_or_inspiration_provenance_appears(self):
        """The DesignSpec boundary, as narrowed in Phase 21: the concept's NAME is
        admitted as ``display_title`` because a gallery cannot be navigated without
        one, and the DESCRIPTION is not. This asserts the description half — every
        spec field that says what the garment actually looks like stays out, so
        admitting the name cannot be read as admitting the spec."""
        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser)
        create_ready_design_version(
            design_id,
            image_prompt="A scarlet lehenga, full length, plain background.",
            with_storage_objects=False,
        )

        raw = gallery(browser).content.decode()
        for forbidden in (
            "scarlet lehenga",
            "image_prompt",
            "prompt_builder_version",
            "design_spec",
            "inspiration_context",
            "image_processor_version",
            # The spec's descriptive fields, named individually so a future
            # widening of this payload has to delete an assertion to happen.
            "concept_summary",
            "garment_breakdown",
            "colour_story",
            "fabrics_and_texture",
            "image_alt_text",
            "embroidery",
        ):
            assert forbidden not in raw, forbidden

    def test_no_annotation_note_text_appears(self):
        """A note is the most personal free text in the product (CLAUDE.md §7).
        The gallery has no business carrying one, and this is the payload most
        likely to grow one by accident later."""
        from sitara.designs.models import DesignAnnotationDocument

        browser, token = signed_in_client()
        design_id = create_owned_design_id(browser)
        version = create_ready_design_version(design_id, with_storage_objects=False)
        DesignAnnotationDocument.objects.create(
            design_version=version,
            revision=1,
            schema_version=1,
            document={
                "schema_version": 1,
                "items": [
                    {
                        "id": "1",
                        "type": "pin",
                        "note": "The neckline is far too low for my family",
                        "palette": "ink",
                        "geometry": {"x": 0.5, "y": 0.5},
                    }
                ],
            },
        )

        raw = gallery(browser).content.decode()
        assert "neckline" not in raw
        assert "family" not in raw

    def test_another_account_s_concepts_never_appear(self):
        mine, _ = signed_in_client(unique_email())
        theirs, _ = signed_in_client(unique_email())
        my_id = create_owned_design_id(mine, title="Mine")
        their_id = create_owned_design_id(theirs, title="Theirs")

        ids = [row["id"] for row in gallery(mine).json()["designs"]]
        assert ids == [my_id]
        assert their_id not in ids
        assert "Theirs" not in gallery(mine).content.decode()

    def test_an_anonymous_browser_sees_only_its_own_session_s_designs(self):
        anonymous = csrf_client()
        token = bootstrap_csrf(anonymous)
        create_design(anonymous, title="Anonymous concept", token=token)
        stranger = csrf_client()

        assert gallery(stranger).json()["designs"] == []
        assert len(gallery(anonymous).json()["designs"]) == 1


class TestPaging:
    def test_newest_first_and_bounded_by_default(self):
        browser, token = signed_in_client()
        for index in range(3):
            create_design(browser, title=f"concept {index}", token=token)

        body = gallery(browser).json()
        assert [row["title"] for row in body["designs"]] == ["concept 2", "concept 1", "concept 0"]
        assert body["total"] == 3

    def test_offset_walks_the_list_without_repeating_or_skipping(self):
        browser, token = signed_in_client()
        for index in range(5):
            create_design(browser, title=f"concept {index}", token=token)

        first = gallery(browser, "?limit=2&offset=0").json()
        second = gallery(browser, "?limit=2&offset=2").json()
        third = gallery(browser, "?limit=2&offset=4").json()
        seen = [row["title"] for page in (first, second, third) for row in page["designs"]]
        assert seen == ["concept 4", "concept 3", "concept 2", "concept 1", "concept 0"]
        assert first["total"] == second["total"] == third["total"] == 5
        assert len(third["designs"]) == 1

    def test_an_offset_past_the_end_is_an_empty_page_not_an_error(self):
        browser, token = signed_in_client()
        create_design(browser, title="only one", token=token)
        body = gallery(browser, "?offset=50").json()
        assert body["designs"] == []
        assert body["total"] == 1

    def test_an_oversized_limit_is_clamped_rather_than_refused(self):
        """Asking for more than the ceiling has a correct answer, so it gets one."""
        browser, _ = signed_in_client()
        body = gallery(browser, f"?limit={GALLERY_PAGE_SIZE_MAX + 500}").json()
        assert body["limit"] == GALLERY_PAGE_SIZE_MAX

    def test_a_zero_limit_falls_back_rather_than_returning_an_ambiguous_empty_page(self):
        browser, token = signed_in_client()
        create_design(browser, title="only one", token=token)
        body = gallery(browser, "?limit=0").json()
        assert body["limit"] == GALLERY_PAGE_SIZE_DEFAULT
        assert len(body["designs"]) == 1

    @pytest.mark.parametrize(
        "query",
        [
            "?limit=banana",
            "?limit=-1",
            "?offset=-1",
            "?offset=two",
            "?limit=1.5",
        ],
    )
    def test_a_nonsense_page_is_refused_rather_than_silently_becoming_page_one(self, query):
        """A client with a bug is told about it, exactly as an unknown write field
        is rejected rather than ignored — silence teaches clients they worked."""
        browser, _ = signed_in_client()
        response = gallery(browser, query)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_failed"

    def test_an_offset_beyond_the_ceiling_is_refused_rather_than_reaching_the_database(self):
        """Django writes LIMIT/OFFSET into the SQL with ``%d`` interpolation rather
        than as a bound parameter, so an arbitrary-precision Python int becomes an
        arbitrary-precision SQL literal. PostgreSQL rejects one past bigint, and a
        raw database error is not an APIException — DRF would not wrap it, and the
        caller would get an HTML 500 instead of this project's JSON envelope."""
        browser, _ = signed_in_client()
        response = gallery(browser, f"?offset={GALLERY_OFFSET_MAX + 1}")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_failed"
        assert "offset" in response.json()["error"]["fields"]

    def test_the_ceiling_stays_clear_of_the_database_limit_it_exists_to_respect(self):
        """The invariant behind ``GALLERY_OFFSET_MAX`` is not a ratio to the page
        size — it is that the largest slice the view can build stays inside a
        signed 64-bit integer. Asserted here rather than as a module-level
        ``assert``, which ``python -O`` would strip out of the running code."""
        assert GALLERY_OFFSET_MAX + GALLERY_PAGE_SIZE_MAX < 2**63

    def test_the_ceiling_offset_itself_still_answers(self):
        """The boundary is inclusive, so the refusal is about impossible values
        rather than a fencepost a legitimate deep page could trip over."""
        browser, _ = signed_in_client()
        body = gallery(browser, f"?offset={GALLERY_OFFSET_MAX}").json()
        assert body["designs"] == []
        assert body["offset"] == GALLERY_OFFSET_MAX

    def test_a_refused_page_names_the_field_without_echoing_the_value(self):
        browser, _ = signed_in_client()
        response = gallery(browser, "?limit=<script>alert(1)</script>")
        assert response.status_code == 400
        body = response.content.decode()
        assert "limit" in response.json()["error"]["fields"]
        assert "script" not in body


class TestQueryCount:
    def test_the_query_count_does_not_grow_with_the_number_of_concepts(self):
        """The N+1 this payload invites twice over: once per design for its
        versions, and once per version for the attempt behind it. Both are
        prefetched, so the count is flat — measured at two sizes and compared,
        rather than pinned to one magic number, because a single number proves
        nothing about growth and breaks on any unrelated middleware change."""
        browser, token = signed_in_client()

        def build(count: int):
            for _ in range(count):
                design_id = create_owned_design_id(browser)
                version = create_ready_design_version(design_id, with_storage_objects=False)
                attempt_for(version)

        def count_queries() -> int:
            with CaptureQueriesContext(connection) as captured:
                response = gallery(browser)
                assert response.status_code == 200
            return len(captured.captured_queries)

        build(1)
        one_concept = count_queries()

        build(4)
        five_concepts = count_queries()
        assert len(gallery(browser).json()["designs"]) == 5
        assert five_concepts == one_concept, (
            f"the gallery asked {five_concepts} queries for five concepts and "
            f"{one_concept} for one — the per-design or per-version prefetch is gone"
        )
