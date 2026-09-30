from scripts.attribution_reporting_labels import (
    append_reporting_definition_labels,
    b4_dominance_flag,
    cross_window_sign_stability,
    reporting_definition_labels,
)


def test_b4_dominance_flag_true_when_b4_strictly_exceeds_observable_sum() -> None:
    assert b4_dominance_flag(-0.1047, 0.0484) is True


def test_b4_dominance_flag_false_when_exactly_equal() -> None:
    assert b4_dominance_flag(-0.05, 0.05) is False


def test_cross_window_sign_stability_insufficient_for_zero_or_one_window() -> None:
    assert cross_window_sign_stability([]) == "insufficient_windows"
    assert cross_window_sign_stability([0.10]) == "insufficient_windows"
    assert cross_window_sign_stability([None, -0.10]) == "insufficient_windows"


def test_cross_window_sign_stability_detects_sign_flip() -> None:
    assert cross_window_sign_stability([0.1619, -0.1047]) == "sign_flip"


def test_cross_window_sign_stability_detects_same_sign() -> None:
    assert cross_window_sign_stability([0.16, 0.03]) == "same_sign"
    assert cross_window_sign_stability([-0.16, -0.03]) == "same_sign"


def test_cross_window_sign_stability_keeps_zero_cases_explicit() -> None:
    assert cross_window_sign_stability([0.0, 0.03]) == "mixed_or_zero"
    assert cross_window_sign_stability([0.0, 0.0]) == "mixed_or_zero"


def test_reporting_definition_labels_default_c1_basis_label() -> None:
    labels = reporting_definition_labels(b4_value=-0.1047, observable_timing_sum=0.0484)

    assert labels["c1_basis_label"] == "nav_unexplained_target"
    assert labels["label_version"] == "rd006_minimal_v1"


def test_append_reporting_definition_labels_preserves_existing_fields() -> None:
    payload = {
        "status": "pass_for_pm_review",
        "reconciliation": {
            "observable_b1_b2_b3_sum_pct": 0.0484,
        },
    }

    labeled = append_reporting_definition_labels(
        payload,
        b4_value=-0.1047,
        observable_timing_sum=0.0484,
    )

    assert labeled is not payload
    assert labeled["status"] == "pass_for_pm_review"
    assert labeled["reconciliation"] == payload["reconciliation"]
    assert "reporting_definition_labels" not in payload
    assert labeled["reporting_definition_labels"]["b4_dominance_flag"] is True
