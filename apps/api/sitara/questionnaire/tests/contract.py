"""Locate and load the shared cross-language validation contract.

The fixture lives at the repository root (``contracts/questionnaire-
validation-cases.json``) so both the Django tests and the frontend Vitest
tests consume the SAME file. It is found by walking up from this test module
(and the working directory) until a ``contracts`` directory containing the
file is reached — which resolves correctly both in CI (repo root checked out)
and in the local API container (bind-mounted read-only to ``/contracts``,
see ``compose.yaml``)."""

import json
import pathlib

_CONTRACT_RELATIVE = pathlib.Path("contracts") / "questionnaire-validation-cases.json"


def _find_contract() -> pathlib.Path:
    starts = [pathlib.Path(__file__).resolve(), pathlib.Path.cwd().resolve()]
    for start in starts:
        for parent in [start, *start.parents]:
            candidate = parent / _CONTRACT_RELATIVE
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(
        "Shared questionnaire validation contract not found. Expected "
        f"'{_CONTRACT_RELATIVE}' at the repository root (copied to /contracts "
        "in the API container)."
    )


def load_contract() -> dict:
    with _find_contract().open(encoding="utf-8") as handle:
        return json.load(handle)
