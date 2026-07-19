"""Shared helpers for the design test suites.

All API flows run with REAL CSRF enforcement (enforce_csrf_checks=True).
Auth calls get a unique REMOTE_ADDR and email per test so the Redis-backed
authentication rate limiters never bleed between tests."""

import json
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import Client
from django.utils import timezone

from sitara.designs.models import DesignVersion
from sitara.questionnaire.models import QuestionnaireVersion
from sitara.questionnaire.tests.contract import load_contract

STRONG_PASSWORD = "Correct-Horse-Battery-2026!"

DESIGNS_URL = "/api/v1/designs/"

# The shared contract's compact schema doubles as a realistic active
# questionnaire for draft-API tests (garment → silhouette restrictions,
# saree draping visibility, colour/embellishment constraints, capped notes).
CONTRACT = load_contract()
CONTRACT_SCHEMA = CONTRACT["schema"]

# A complete, valid set of answers for CONTRACT_SCHEMA (lehenga path).
COMPLETE_ANSWERS = {
    "garment_type": "lehenga",
    "silhouette": "a_line_lehenga",
    "colour_palette": ["red", "gold"],
    "embellishment_styles": ["zardozi"],
}


def design_url(design_id) -> str:
    return f"{DESIGNS_URL}{design_id}/"


def validate_url(design_id) -> str:
    return f"{DESIGNS_URL}{design_id}/validate/"


def make_active_questionnaire(*, version: int = 1) -> QuestionnaireVersion:
    """An ACTIVE questionnaire version carrying the shared contract schema."""
    return QuestionnaireVersion.objects.create(
        version=version, status="active", schema=CONTRACT_SCHEMA
    )


def unique_email() -> str:
    return f"designer-{uuid.uuid4().hex[:12]}@example.test"


def unique_ip() -> str:
    raw = uuid.uuid4().bytes
    return f"10.{raw[0]}.{raw[1]}.{raw[2]}"


def csrf_client() -> Client:
    return Client(enforce_csrf_checks=True)


def bootstrap_csrf(client: Client) -> str:
    response = client.get("/api/v1/auth/csrf/")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def send_json(client: Client, method: str, url: str, data=None, token=None, ip=None):
    extra = {"REMOTE_ADDR": ip or unique_ip()}
    if token is not None:
        extra["HTTP_X_CSRFTOKEN"] = token
    return getattr(client, method)(
        url,
        data=json.dumps(data if data is not None else {}),
        content_type="application/json",
        **extra,
    )


def create_design(client: Client, title=None, token: str | None = None):
    token = token or bootstrap_csrf(client)
    payload = {} if title is None else {"title": title}
    return send_json(client, "post", DESIGNS_URL, payload, token=token)


def create_owned_design_id(client: Client, *, title: str = "Test design") -> str:
    response = create_design(client, title=title)
    assert response.status_code == 201, response.content
    return response.json()["id"]


def register(client: Client, email: str, password: str = STRONG_PASSWORD):
    token = bootstrap_csrf(client)
    response = send_json(
        client,
        "post",
        "/api/v1/auth/register/",
        {"email": email, "password": password, "password_confirm": password},
        token=token,
    )
    assert response.status_code == 201, response.content
    return response


def login(client: Client, email: str, password: str = STRONG_PASSWORD):
    token = bootstrap_csrf(client)
    response = send_json(
        client,
        "post",
        "/api/v1/auth/login/",
        {"email": email, "password": password},
        token=token,
    )
    assert response.status_code == 200, response.content
    return response


def logout(client: Client):
    token = bootstrap_csrf(client)
    response = send_json(client, "post", "/api/v1/auth/logout/", {}, token=token)
    assert response.status_code == 200, response.content
    return response


def create_ready_design_version(
    design_id,
    *,
    version_number: int = 1,
    design_spec: dict | None = None,
    schema_version: int = 1,
    image_prompt: str = "A test prompt.",
    with_storage_objects: bool = True,
) -> DesignVersion:
    """A DesignVersion with every DesignSpec and permanent-image provenance
    field populated — the shared "fully ready" fixture for both the Phase 11
    image-delivery tests and the Phase 12 result-endpoint tests. Pass
    `with_storage_objects=False` when a test only needs the database row
    (e.g. the result endpoint, which never checks object-store existence)."""
    version = DesignVersion.objects.create(
        design_id=design_id,
        version_number=version_number,
        design_spec=design_spec if design_spec is not None else {"schema_version": 1},
        design_spec_schema_version=schema_version,
        design_spec_template_version="v1",
        design_spec_provider="fixture",
        design_spec_model="fixture-model",
        design_spec_generated_at=timezone.now(),
        image_prompt=image_prompt,
        prompt_builder_version="3.0.0",
        image_storage_key=f"design-images/{design_id}/v1/original.webp",
        image_sha256="a" * 64,
        image_size_bytes=1000,
        image_width=1536,
        image_height=2048,
        thumbnail_storage_key=f"design-images/{design_id}/v1/thumbnail.webp",
        thumbnail_sha256="b" * 64,
        thumbnail_size_bytes=100,
        thumbnail_width=384,
        thumbnail_height=512,
        image_processor_version="1.0.0",
        image_ingested_at=timezone.now(),
    )
    if with_storage_objects:
        store = storages["design_images"]
        store.save(version.image_storage_key, ContentFile(b"original"))
        store.save(version.thumbnail_storage_key, ContentFile(b"thumbnail"))
    return version
