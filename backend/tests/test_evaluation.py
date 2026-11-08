import pytest
from itertools import product

from app.evaluation import classification, evaluate, match_campaigns


def test_classification_refuses_labels_or_predictions_outside_universe():
    with pytest.raises(ValueError):
        classification({"unknown"}, set(), {"tx"})
    with pytest.raises(ValueError):
        classification(set(), {"unknown"}, {"tx"})


def test_undefined_denominators_are_not_reported_as_perfect():
    result = classification(set(), set(), set())
    assert all(result[key] is None for key in ("precision", "recall", "F1", "FPR"))


def test_two_campaigns_cannot_share_one_alert():
    assessments = [(str(i), {"score": 0, "hits": []}) for i in range(4)]
    campaigns = [
        {"id": "a", "pattern": "STRUCTURING", "tx_hashes": ["0", "1"]},
        {"id": "b", "pattern": "STRUCTURING", "tx_hashes": ["2", "3"]},
    ]
    result = evaluate(
        {}, assessments, [{"id": "one", "code": "STRUCTURING", "evidence": ["0", "2"]}], campaigns=campaigns
    )
    assert result["campaign_count"] == 2
    assert result["campaigns_detected"] == 1


def test_matching_reassigns_alert_to_find_maximum_cardinality():
    assessments = [(str(i), {"score": 0, "hits": []}) for i in range(4)]
    campaigns = [
        {"id": "a", "pattern": "STRUCTURING", "tx_hashes": ["0", "1"]},
        {"id": "b", "pattern": "STRUCTURING", "tx_hashes": ["2", "3"]},
    ]
    alerts = [
        {"id": "flexible", "code": "STRUCTURING", "evidence": ["0", "1", "2", "3"]},
        {"id": "only-a", "code": "STRUCTURING", "evidence": ["0"]},
    ]
    first = evaluate({}, assessments, alerts, campaigns=campaigns)
    second = evaluate({}, assessments, list(reversed(alerts)), campaigns=list(reversed(campaigns)))
    assert first["campaigns_detected"] == 2
    assert first["campaigns"] == second["campaigns"]
    assert len({c["matched_alert_id"] for c in first["campaigns"]}) == 2


def test_matching_agrees_with_exhaustive_oracle_for_all_three_by_three_graphs():
    campaigns = [{"id": str(i), "pattern": "STRUCTURING", "tx_hashes": [str(i)]} for i in range(3)]
    for mask in range(2**9):
        eligible = {(c, a) for c in range(3) for a in range(3) if mask & (1 << (c * 3 + a))}
        alerts = [
            {
                "id": str(a),
                "code": "STRUCTURING",
                "evidence": [str(c) for c in range(3) if (c, a) in eligible],
            }
            for a in range(3)
        ]
        oracle = max(
            sum(a >= 0 for a in assignment)
            for assignment in product(range(-1, 3), repeat=3)
            if len([a for a in assignment if a >= 0]) == len({a for a in assignment if a >= 0})
            and all(a < 0 or (c, a) in eligible for c, a in enumerate(assignment))
        )
        matches = match_campaigns(campaigns, alerts)
        assert sum(c["detected"] for c in matches) == oracle, mask
