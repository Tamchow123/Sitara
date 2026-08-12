"""A refined canonical selection must still be a legal questionnaire answer.

Phase 23, ADR 0028. Before it, ``source_selections`` was trustworthy for one
reason: it was verified as an exact echo of already-validated questionnaire
answers, and no refinement could touch it. Once a refinement may change it that
guarantee is gone, and nothing else replaces it — a model can answer
``"fabrics": ["unobtainium"]`` and the deterministic prompt builder will render
it faithfully, because the builder's job is to render what it is given.

This module is the replacement guarantee, and it is deliberately as strong as
the one it replaces: the refined selections must be a set of answers the design's
own questionnaire would have accepted from the user in the first place.

Three things are load-bearing:

- **The design's PINNED version, never the active one.**
  ``Design.questionnaire_version`` is assign-once, and a design pinned to a
  RETIRED version must stay refinable — Phase 9's follow-up established and
  tested that path. Validating against whatever happens to be active today would
  reject a perfectly good refinement of an older concept, or worse, accept a
  value that version never offered.
- **No second rule evaluator.**
  :func:`~sitara.questionnaire.answer_validation.validate_questionnaire_answers`
  (and through it :mod:`sitara.questionnaire.rules`) stays the single authority
  for declared-option membership, ``restrict_options`` consistency, exclusivity,
  item counts, answer shape and colour-palette membership. CLAUDE.md §12 forbids
  duplicating those rules, and a second copy would drift.
- **Selections alone are a complete answer set.**
  Every required question in every committed questionnaire version is a
  ``source_selections`` field, so validating the selections on their own with
  ``require_complete=True`` is exact rather than approximate. A structural test
  pins that invariant; if a future version adds a required question outside the
  selection set, this fails CLOSED (it refuses the refinement) and that test says
  why.

Failure is always the controlled :class:`RefinedSelectionsInvalid`, carrying only
the question ids that failed — never the rejected value, never the questionnaire
contents, never a raw validator message.
"""

from sitara.questionnaire.answer_validation import (
    QuestionnaireAnswerError,
    validate_questionnaire_answers,
)


class RefinedSelectionsInvalid(Exception):
    """The refined canonical selections are not a legal answer set for the
    design's own pinned questionnaire version.

    ``fields`` is the sorted list of question ids that failed — safe to log,
    because a question id is a source-controlled machine name, not user data.
    The rejected VALUES are never carried."""

    def __init__(self, fields):
        self.fields = sorted(fields)
        super().__init__("the refined selections are not valid for this questionnaire")


class RefinementQuestionnaireUnavailable(Exception):
    """The design has no usable pinned questionnaire version, so no refined
    selection can be validated against anything.

    Fail closed: a refinement that cannot be checked is refused, never accepted
    on trust."""


def selections_as_answers(selections: dict) -> dict:
    """The ``source_selections`` echo as a questionnaire answer object.

    The mapping is 1:1 by design — a ``source_selections`` field IS the
    questionnaire question of the same name (see
    :mod:`sitara.generation.context`) — so this only has to drop the entries
    that mean "unanswered". A null scalar and an empty list are both absences,
    and an absence must be absent rather than present-and-empty: an answer of
    ``[]`` to a hidden question is still an answer, and the validator would
    rightly reject it."""
    return {
        field: value
        for field, value in selections.items()
        if value is not None and value != [] and value != ""
    }


def assert_refined_selections_are_answerable(design, refined_spec) -> None:
    """Validate ``refined_spec``'s canonical selections against ``design``'s own
    pinned questionnaire version.

    Raises :class:`RefinementQuestionnaireUnavailable` when the design has no
    pinned version to check against, or :class:`RefinedSelectionsInvalid` when
    the selections are not an answer set that version would accept. Returns
    ``None`` on success. Pure with respect to the database beyond reading the
    pinned version's schema: it persists nothing and mutates nothing."""
    version = design.questionnaire_version
    if version is None or not isinstance(version.schema, dict):
        raise RefinementQuestionnaireUnavailable(
            "this design has no questionnaire version to validate against"
        )

    answers = selections_as_answers(refined_spec.source_selections.model_dump(mode="json"))
    try:
        validate_questionnaire_answers(version.schema, answers, require_complete=True)
    except QuestionnaireAnswerError as exc:
        raise RefinedSelectionsInvalid(exc.errors) from None
