from smc_analysis.config import ConditionConfig, RuleConfig
from smc_analysis.rules import evaluate_condition, evaluate_rule


class TestEvaluateCondition:
    def test_numeric_greater_than(self):
        cond = ConditionConfig(field="locked_liquidity_pct", operator=">", value=90)
        assert evaluate_condition({"locked_liquidity_pct": "95.0"}, cond) is True
        assert evaluate_condition({"locked_liquidity_pct": "80.0"}, cond) is False

    def test_boolean_equality(self):
        cond = ConditionConfig(field="rugged", operator="==", value=False)
        assert evaluate_condition({"rugged": "False"}, cond) is True
        assert evaluate_condition({"rugged": "True"}, cond) is False

    def test_missing_field_is_none_not_false(self):
        cond = ConditionConfig(field="locked_liquidity_pct", operator=">", value=90)
        assert evaluate_condition({}, cond) is None

    def test_empty_string_field_is_none(self):
        cond = ConditionConfig(field="locked_liquidity_pct", operator=">", value=90)
        assert evaluate_condition({"locked_liquidity_pct": ""}, cond) is None

    def test_unparseable_numeric_value_is_none(self):
        cond = ConditionConfig(field="locked_liquidity_pct", operator=">", value=90)
        assert evaluate_condition({"locked_liquidity_pct": "n/a"}, cond) is None


class TestEvaluateRule:
    def test_all_conditions_true_is_true(self):
        rule = RuleConfig(
            id="r1", description="x", type="securite",
            conditions=(
                ConditionConfig(field="rugged", operator="==", value=False),
                ConditionConfig(field="score_normalised", operator="<", value=30),
            ),
        )
        row = {"rugged": "False", "score_normalised": "10"}
        assert evaluate_rule(row, rule) is True

    def test_one_condition_false_is_false(self):
        rule = RuleConfig(
            id="r1", description="x", type="securite",
            conditions=(
                ConditionConfig(field="rugged", operator="==", value=False),
                ConditionConfig(field="score_normalised", operator="<", value=30),
            ),
        )
        row = {"rugged": "False", "score_normalised": "80"}
        assert evaluate_rule(row, rule) is False

    def test_missing_field_in_any_condition_excludes_whole_rule(self):
        rule = RuleConfig(
            id="r1", description="x", type="securite",
            conditions=(
                ConditionConfig(field="rugged", operator="==", value=False),
                ConditionConfig(field="score_normalised", operator="<", value=30),
            ),
        )
        row = {"rugged": "False"}  # score_normalised absent
        assert evaluate_rule(row, rule) is None

    def test_never_partial_and(self):
        """Même si la première condition est vraie et la seconde manquante,
        le résultat est l'exclusion, jamais 'vrai parce que la première l'était'."""
        rule = RuleConfig(
            id="r1", description="x", type="technique",
            conditions=(
                ConditionConfig(field="locked_liquidity_pct", operator=">", value=0),
                ConditionConfig(field="pool_fee_pct", operator=">=", value=0),
            ),
        )
        row = {"locked_liquidity_pct": "100"}
        assert evaluate_rule(row, rule) is None
