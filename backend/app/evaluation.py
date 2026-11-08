"""Evaluator only. Labels never enter the AML engine."""

from collections import defaultdict, deque


def classification(actual: set, predicted: set, universe: set) -> dict:
    if not actual <= universe or not predicted <= universe:
        raise ValueError("Labels and predictions must belong to the evaluated universe")
    tp, fp, fn = len(actual & predicted), len(predicted - actual), len(actual - predicted)
    tn = len(universe - (actual | predicted))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "TN": tn,
        "precision": precision,
        "recall": recall,
        "FPR": fp / (fp + tn) if fp + tn else None,
        "F1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def match_campaigns(campaigns: list[dict], alerts: list[dict]) -> list[dict]:
    """Deterministic maximum-cardinality matching; an episode can claim one campaign."""
    campaigns = sorted(campaigns, key=lambda c: c["id"])
    if len({c["id"] for c in campaigns}) != len(campaigns):
        raise ValueError("Duplicate campaign identity")
    alerts = [{**a, "id": a.get("id", f"alert-{i}")} for i, a in enumerate(alerts)]
    if len({a["id"] for a in alerts}) != len(alerts):
        raise ValueError("Duplicate alert identity")
    overlaps, candidates = {}, {}
    for campaign in campaigns:
        core = set(campaign["tx_hashes"])
        if not core:
            raise ValueError("Campaign core cannot be empty")
        eligible = []
        for alert in alerts:
            if alert["code"] != campaign["pattern"]:
                continue
            count = len(core & set(alert["evidence"]))
            overlaps[campaign["id"], alert["id"]] = count / len(core)
            if count * 2 >= len(core):
                eligible.append((count, alert["id"]))
        candidates[campaign["id"]] = [
            identity for _, identity in sorted(eligible, key=lambda p: (-p[0], p[1]))
        ]
    by_alert, by_campaign = {}, {}
    for root in candidates:
        pending, parents, visited = deque([root]), {}, {root}
        free = None
        while pending and free is None:
            current = pending.popleft()
            for alert_id in candidates[current]:
                if alert_id in parents:
                    continue
                parents[alert_id] = current
                if alert_id not in by_alert:
                    free = alert_id
                    break
                previous = by_alert[alert_id]
                if previous not in visited:
                    visited.add(previous)
                    pending.append(previous)
        while free is not None:
            current = parents[free]
            previous = by_campaign.get(current)
            by_campaign[current], by_alert[free] = free, current
            free = previous
    return [
        {
            "id": c["id"],
            "pattern": c["pattern"],
            "detected": c["id"] in by_campaign,
            "matched_alert_id": by_campaign.get(c["id"]),
            "core_overlap": overlaps.get((c["id"], by_campaign.get(c["id"])), 0),
        }
        for c in campaigns
    ]


def evaluate(
    truth: dict[str, list[str]],
    assessments: list[tuple[str, dict]],
    alerts: list[dict],
    *,
    campaigns: list[dict] | None = None,
) -> dict:
    universe = {h for h, _ in assessments}
    if len(universe) != len(assessments):
        raise ValueError("Online evaluation requires exactly one prediction per transaction")
    if campaigns is None:
        campaigns = [
            {"id": pattern, "pattern": pattern, "tx_hashes": hashes} for pattern, hashes in truth.items()
        ]
    truth = defaultdict(set)
    for campaign in campaigns:
        truth[campaign["pattern"]].update(campaign["tx_hashes"])
    matches = match_campaigns(campaigns, alerts)
    actual = set().union(*(set(v) for v in truth.values())) if truth else set()
    online = {h for h, a in assessments if a["score"] >= 70}
    retrospective = set().union(*(set(a["evidence"]) for a in alerts)) if alerts else set()
    by_pattern = {}
    for pattern in sorted(truth):
        hashes = truth[pattern]
        expected = set(hashes)
        predicted = {h for h, a in assessments if any(hit["code"] == pattern for hit in a["hits"])}
        relevant = [a for a in alerts if a["code"] == pattern]
        evidence = set().union(*(set(a["evidence"]) for a in relevant)) if relevant else set()
        pattern_matches = [c for c in matches if c["pattern"] == pattern]
        match = max((c["core_overlap"] for c in pattern_matches), default=0)
        by_pattern[pattern] = {
            "online": classification(expected, predicted, universe),
            "retrospective": classification(expected, evidence, universe),
            "campaign_detected": match >= 0.5,
            "core_overlap": match,
            "campaign_count": len(pattern_matches),
            "campaigns_detected": sum(c["detected"] for c in pattern_matches),
        }
    counts = defaultdict(int)
    for a in alerts:
        counts[a["code"]] += 1
    return {
        "scope": "single synthetic run; not an independent held-out benchmark",
        "transaction_count": len(universe),
        "labelled_transaction_count": len(actual),
        "prevalence": len(actual) / len(universe) if universe else 0,
        "online": classification(actual, online, universe),
        "retrospective": classification(actual, retrospective, universe),
        "patterns": by_pattern,
        "campaigns_detected": sum(c["detected"] for c in matches),
        "campaign_count": len(campaigns),
        "campaigns": matches,
        "matching_policy": "maximum cardinality; same pattern, >=50% core; one alert per campaign",
        "detectors_without_campaign_labels": sorted({a["code"] for a in alerts} - set(truth)),
        "alerts_by_detector": dict(counts),
        "alerts_per_1000_tx": len(alerts) * 1000 / len(universe) if universe else 0,
    }
