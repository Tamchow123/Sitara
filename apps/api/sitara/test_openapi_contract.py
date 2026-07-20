"""Phase 6 — OpenAPI contract tests.

These prove the generated contract is complete, safe and byte-deterministic:
generation is warning-free and validates, every canonical operation is
present (and nothing else, including Django admin), paths are clean, the
CSRF header is documented on unsafe browser operations, password fields are
write-only, the catalogue image endpoints expose binary WebP, the
questionnaire schema is structurally typed, no JWT/bearer scheme exists, no
private field leaks into any component, and the committed
``apps/api/openapi/schema.json`` matches a fresh regeneration exactly.
"""

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})

SCHEMA_PATH = Path(settings.BASE_DIR) / "openapi" / "schema.json"

# The exact canonical operation set (A3) — trailing slashes as documented.
EXPECTED_OPERATIONS = frozenset(
    {
        ("/api/v1/health/live", "get"),
        ("/api/v1/health/ready", "get"),
        ("/api/v1/config/public", "get"),
        ("/api/v1/auth/csrf/", "get"),
        ("/api/v1/auth/register/", "post"),
        ("/api/v1/auth/login/", "post"),
        ("/api/v1/auth/logout/", "post"),
        ("/api/v1/auth/me/", "get"),
        ("/api/v1/designs/", "get"),
        ("/api/v1/designs/", "post"),
        ("/api/v1/designs/{design_id}/", "get"),
        ("/api/v1/designs/{design_id}/", "patch"),
        ("/api/v1/designs/{design_id}/validate/", "post"),
        ("/api/v1/designs/{design_id}/generate/", "post"),
        ("/api/v1/designs/{design_id}/refine/", "post"),
        ("/api/v1/designs/{design_id}/versions/{version_id}/images/", "get"),
        ("/api/v1/designs/{design_id}/versions/{version_id}/result/", "get"),
        ("/api/v1/jobs/{job_id}/", "get"),
        ("/api/v1/questionnaire/active/", "get"),
        ("/api/v1/inspiration-assets/", "get"),
        ("/api/v1/inspiration-assets/{asset_id}/image/", "get"),
        ("/api/v1/inspiration-assets/{asset_id}/thumbnail/", "get"),
    }
)

# Unsafe browser operations that MUST document the CSRF header.
UNSAFE_OPERATIONS = frozenset(
    {
        ("/api/v1/auth/register/", "post"),
        ("/api/v1/auth/login/", "post"),
        ("/api/v1/auth/logout/", "post"),
        ("/api/v1/designs/", "post"),
        ("/api/v1/designs/{design_id}/", "patch"),
        ("/api/v1/designs/{design_id}/validate/", "post"),
        ("/api/v1/designs/{design_id}/generate/", "post"),
        ("/api/v1/designs/{design_id}/refine/", "post"),
    }
)

# Property names that must never appear anywhere in the public contract.
FORBIDDEN_PROPERTY_NAMES = frozenset(
    {
        "password_hash",
        "is_staff",
        "is_superuser",
        "is_active",
        "session_key",
        "sessionid",
        "design_session",
        "design_session_id",
        "storage_key",
        "image_storage_key",
        "thumbnail_storage_key",
        "image_sha256",
        "sha256",
        "evidence_reference",
        "internal_notes",
        "rights_notes",
        "verified_by",
        "approved_by",
        "uploaded_by",
        "anthropic_api_key",
        "replicate_api_token",
        "last_login",
    }
)


def _operations(schema: dict):
    return {
        (path, method)
        for path, methods in schema["paths"].items()
        for method in methods
        if method in HTTP_METHODS
    }


def _property_names(node) -> set[str]:
    """Every ``properties`` key anywhere under ``node`` (recursively)."""
    names: set[str] = set()

    def walk(obj):
        if isinstance(obj, dict):
            props = obj.get("properties")
            if isinstance(props, dict):
                names.update(props.keys())
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(node)
    return names


@pytest.fixture(scope="module")
def committed_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_generation_is_warning_free_and_validates(tmp_path):
    # --validate --fail-on-warn make the command raise on ANY warning, error
    # or invalid OpenAPI document. A clean run proves 1, 2 and generation.
    out = tmp_path / "schema.json"
    call_command(
        "spectacular",
        "--format",
        "openapi-json",
        "--file",
        str(out),
        "--validate",
        "--fail-on-warn",
    )
    assert out.exists()


def test_committed_schema_is_byte_deterministic(tmp_path):
    out = tmp_path / "schema.json"
    call_command("spectacular", "--format", "openapi-json", "--file", str(out))
    assert (
        out.read_bytes() == SCHEMA_PATH.read_bytes()
    ), "apps/api/openapi/schema.json is stale — regenerate it."


def test_operation_ids_are_unique(committed_schema):
    ids = [
        committed_schema["paths"][path][method]["operationId"]
        for path, method in _operations(committed_schema)
    ]
    assert len(ids) == len(set(ids))


def test_exactly_the_canonical_operations_are_present(committed_schema):
    assert _operations(committed_schema) == EXPECTED_OPERATIONS


def test_no_admin_endpoint_is_documented(committed_schema):
    assert not any("admin" in path for path in committed_schema["paths"])


def test_paths_have_no_regex_or_optional_slash(committed_schema):
    for path in committed_schema["paths"]:
        assert "/?" not in path, path
        for fragment in ("^", "$", "\\", "(", ")", "[", "]", "?"):
            assert fragment not in path, (path, fragment)


def test_csrf_header_documented_on_unsafe_operations(committed_schema):
    for path, method in UNSAFE_OPERATIONS:
        parameters = committed_schema["paths"][path][method].get("parameters", [])
        header_names = {p["name"] for p in parameters if p.get("in") == "header"}
        assert "X-CSRFToken" in header_names, (path, method)


def test_safe_get_operations_do_not_require_csrf(committed_schema):
    for path, method in _operations(committed_schema):
        if method == "get":
            parameters = committed_schema["paths"][path][method].get("parameters", [])
            header_names = {p["name"] for p in parameters if p.get("in") == "header"}
            assert "X-CSRFToken" not in header_names, (path, method)


def test_password_fields_are_write_only(committed_schema):
    schemas = committed_schema["components"]["schemas"]
    for name, component in schemas.items():
        props = component.get("properties", {})
        if "password" in props or "password_confirm" in props:
            # COMPONENT_SPLIT_REQUEST puts write-only fields only in *Request
            # components; a password in a response component would be a leak.
            assert name.endswith("Request"), f"password exposed in response component {name}"


def test_image_endpoints_expose_binary_webp(committed_schema):
    for path in (
        "/api/v1/inspiration-assets/{asset_id}/image/",
        "/api/v1/inspiration-assets/{asset_id}/thumbnail/",
    ):
        response = committed_schema["paths"][path]["get"]["responses"]["200"]
        content = response["content"]
        assert "image/webp" in content, path
        assert content["image/webp"]["schema"].get("format") == "binary", path
        # And the failure modes are documented.
        assert "404" in committed_schema["paths"][path]["get"]["responses"]
        assert "503" in committed_schema["paths"][path]["get"]["responses"]


def test_questionnaire_schema_is_structurally_typed(committed_schema):
    schemas = committed_schema["components"]["schemas"]

    schema_prop = schemas["ActiveQuestionnaireResponse"]["properties"]["schema"]
    assert schema_prop.get("$ref", "").endswith("/QuestionnaireSchema")

    questionnaire = schemas["QuestionnaireSchema"]["properties"]
    assert {"schema_version", "key", "title", "steps", "rules"} <= set(questionnaire)

    question = schemas["QuestionSchema"]["properties"]
    assert {"id", "type", "label", "required", "options", "constraints"} <= set(question)

    for component in (
        "StepSchema",
        "QuestionOptionSchema",
        "RuleConditionSchema",
        "RuleActionSchema",
    ):
        assert component in schemas, component

    condition = schemas["RuleConditionSchema"]["properties"]
    assert {"question_id", "operator", "values"} <= set(condition)
    action = schemas["RuleActionSchema"]["properties"]
    assert {"action", "question_id"} <= set(action)

    assert set(schemas["TypeEnum"]["enum"]) == {"single_choice", "multi_choice", "text"}
    assert set(schemas["OperatorEnum"]["enum"]) == {"equals", "in", "not_in"}
    assert set(schemas["ActionEnum"]["enum"]) == {"show", "hide", "require", "restrict_options"}


def test_no_jwt_or_bearer_security_scheme(committed_schema):
    schemes = committed_schema.get("components", {}).get("securitySchemes", {})
    assert schemes, "expected a session cookie security scheme"
    for name, scheme in schemes.items():
        assert scheme.get("type") == "apiKey", (name, scheme)
        assert scheme.get("in") == "cookie", (name, scheme)
        assert scheme.get("scheme") != "bearer"
    blob = json.dumps(schemes).lower()
    assert "jwt" not in blob
    assert "bearer" not in blob


def test_public_endpoints_do_not_require_bearer_auth(committed_schema):
    # No operation may reference a bearer/JWT scheme; identity-free public
    # GETs (questionnaire, catalogue) must not demand authentication.
    for path, method in _operations(committed_schema):
        security = committed_schema["paths"][path][method].get("security", [])
        for requirement in security:
            for scheme_name in requirement:
                assert "bearer" not in scheme_name.lower()
                assert "jwt" not in scheme_name.lower()
    for path in (
        "/api/v1/questionnaire/active/",
        "/api/v1/inspiration-assets/",
        "/api/v1/health/live",
    ):
        security = committed_schema["paths"][path]["get"].get("security", [])
        # Either no security block, or one that permits anonymous ({}).
        if security:
            assert {} in security, path


def test_no_private_fields_in_any_component(committed_schema):
    schemas = committed_schema["components"]["schemas"]
    leaked = _property_names(schemas) & FORBIDDEN_PROPERTY_NAMES
    assert not leaked, f"private fields leaked into the contract: {sorted(leaked)}"


def test_register_and_login_advertise_only_json(committed_schema):
    for path in ("/api/v1/auth/register/", "/api/v1/auth/login/"):
        content = committed_schema["paths"][path]["post"]["requestBody"]["content"]
        assert set(content) == {"application/json"}, (path, sorted(content))


def test_logout_has_no_request_body(committed_schema):
    logout = committed_schema["paths"]["/api/v1/auth/logout/"]["post"]
    assert "requestBody" not in logout


def test_no_provider_secrets_or_storage_details_in_schema(committed_schema):
    blob = json.dumps(committed_schema["components"]["schemas"]).lower()
    for forbidden in (
        "replicate",
        "anthropic",
        "secret_access_key",
        "s3_bucket",
        "minio",
        "storage_key",
        "session_key",
    ):
        assert forbidden not in blob, forbidden
