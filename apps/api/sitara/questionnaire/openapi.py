"""Structurally-typed schema serializers for the active-questionnaire response.

Documentation-only. The runtime view still serves the stored ``schema`` JSON
verbatim (see :class:`~sitara.questionnaire.serializers.ActiveQuestionnaireSerializer`);
these serializers exist so the generated TypeScript knows the questionnaire
shape — supported question types, stable question identifiers, labels/help
text, the required flag, option shape, rule operators/actions and rule
condition/target values — instead of an opaque ``object``.

The mirrored allowlists (question types, rule operators, rule actions) come
from :mod:`sitara.questionnaire.schema_validation`, the single authoritative
validator, so the contract cannot drift from what the backend accepts. The
per-question-type ``constraints`` field is represented as a bounded JSON
mapping of the known optional keys rather than a perfect discriminated union
— deliberately, per the phase spec.
"""

from rest_framework import serializers

from .schema_validation import QUESTION_TYPES, RULE_ACTIONS, RULE_OPERATORS

# Sorted for deterministic enum ordering in the committed schema.
_QUESTION_TYPE_CHOICES = sorted(QUESTION_TYPES)
_RULE_OPERATOR_CHOICES = sorted(RULE_OPERATORS)
_RULE_ACTION_CHOICES = sorted(RULE_ACTIONS)


class QuestionOptionSchemaSerializer(serializers.Serializer):
    value = serializers.CharField(help_text="Stable machine identifier persisted in answers.")
    label = serializers.CharField()
    description = serializers.CharField(required=False)
    visual_key = serializers.CharField(
        required=False,
        help_text=(
            "Optional lower-case machine key mapping to a frontend-owned "
            "explanatory visual. Never a URL, path or asset reference."
        ),
    )
    group = serializers.CharField(
        required=False,
        help_text=(
            "Optional lower-case machine group for compact grouped rendering "
            "(e.g. colour groups). Presentation only; never influences generation."
        ),
    )


class QuestionConstraintsSchemaSerializer(serializers.Serializer):
    """Bounded per-question-type constraint mapping (all keys optional).

    ``min_items``/``max_items``/``exclusive_values`` apply to ``multi_choice``;
    ``min_length``/``max_length`` apply to ``text``. Choice questions may omit
    constraints entirely.
    """

    min_items = serializers.IntegerField(required=False)
    max_items = serializers.IntegerField(required=False)
    exclusive_values = serializers.ListField(child=serializers.CharField(), required=False)
    min_length = serializers.IntegerField(required=False)
    max_length = serializers.IntegerField(required=False)


class QuestionSchemaSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Stable machine identifier.")
    type = serializers.ChoiceField(choices=_QUESTION_TYPE_CHOICES)
    label = serializers.CharField()
    help_text = serializers.CharField(required=False)
    required = serializers.BooleanField()
    options = QuestionOptionSchemaSerializer(
        many=True, required=False, help_text="Present for choice questions."
    )
    constraints = QuestionConstraintsSchemaSerializer(required=False)


class StepSchemaSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField(required=False)
    questions = QuestionSchemaSerializer(many=True)


class RuleConditionSchemaSerializer(serializers.Serializer):
    """A rule's ``when`` clause."""

    question_id = serializers.CharField(help_text="The choice question this condition tests.")
    operator = serializers.ChoiceField(choices=_RULE_OPERATOR_CHOICES)
    values = serializers.ListField(child=serializers.CharField())


class RuleActionSchemaSerializer(serializers.Serializer):
    """A rule's ``then`` clause."""

    action = serializers.ChoiceField(choices=_RULE_ACTION_CHOICES)
    question_id = serializers.CharField(help_text="The question the action targets.")
    values = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Option values; present only for restrict_options.",
    )


class CompatibilityRuleSchemaSerializer(serializers.Serializer):
    id = serializers.CharField()
    when = RuleConditionSchemaSerializer()
    then = RuleActionSchemaSerializer()


class QuestionnaireSchemaSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField()
    key = serializers.CharField()
    title = serializers.CharField()
    steps = StepSchemaSerializer(many=True)
    rules = CompatibilityRuleSchemaSerializer(many=True)


class ActiveQuestionnaireResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    version = serializers.IntegerField()
    schema = QuestionnaireSchemaSerializer()
