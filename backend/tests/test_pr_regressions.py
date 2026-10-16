"""Independent counterexamples found while reviewing PRs #1–#4."""

import math
from datetime import timedelta

import networkx as nx
import pytest

from app import aml
from app.aml import Engine
from app.domain import FEE, GENESIS, UNIT, Tx, canonical_hash


def address(i):
    return f"sim_{i:016x}"


def source(i, category="blacklist"):
    return {
        "network_id": "sim-account-1",
        "address": address(i),
        "category": category,
        "severity": 95,
        "confidence": 1.0,
        "source_name": "review fixture",
        "source_ref": "synthetic://review",
        "list_version": "review-v1",
        "known_at": GENESIS.isoformat(),
        "valid_from": GENESIS.isoformat(),
        "valid_to": None,
        "enabled": True,
    }


def transfer(engine, sender, recipient, units, index, seconds):
    transaction = Tx(
        canonical_hash([sender, recipient, units, index]),
        address(sender),
        address(recipient),
        units,
        FEE,
        engine.ledger.nonces.get(address(sender), 0),
        GENESIS + timedelta(seconds=seconds),
        0,
        index,
        index,
    )
    return transaction, engine.process(transaction)


def hit(result, code):
    return next((item for item in result["hits"] if item["code"] == code), None)


def test_u_turn_cannot_return_funds_already_spent_elsewhere():
    engine = Engine({address(1): 2000 * UNIT, address(4): 2000 * UNIT}, [])
    transfer(engine, 1, 2, 1000 * UNIT, 0, 0)
    transfer(engine, 2, 3, 1000 * UNIT - FEE, 1, 60)
    transfer(engine, 4, 2, 1000 * UNIT, 2, 120)
    _, result = transfer(engine, 2, 1, 950 * UNIT, 3, 180)
    assert hit(result, "U_TURN_BOUNCE") is None


def test_u_turn_accepts_later_index_at_same_timestamp():
    engine = Engine({address(1): 2000 * UNIT}, [])
    transfer(engine, 1, 2, 1000 * UNIT, 0, 0)
    _, result = transfer(engine, 2, 1, 950 * UNIT, 1, 0)
    assert hit(result, "U_TURN_BOUNCE") is not None


def test_circular_history_is_independent_of_ti_source_reset():
    engine = Engine({address(1): 2000 * UNIT}, [source(2)])
    transfer(engine, 1, 2, 1000 * UNIT, 0, 0)
    transfer(engine, 2, 3, 1000 * UNIT - FEE, 1, 60)
    _, result = transfer(engine, 3, 1, 1000 * UNIT - 2 * FEE, 2, 120)
    assert hit(result, "CIRCULAR_FLOW") is not None


def test_pagerank_does_not_replace_the_locked_exposure_formula():
    engine = Engine({address(1): 10000 * UNIT}, [source(1)])
    transfer(engine, 1, 2, 5000 * UNIT, 0, 0)
    _, result = transfer(engine, 2, 3, 4000 * UNIT, 1, 60)
    expected = 0.95 * 0.7 * math.exp(-60 / 86400)
    assert result["components"]["E"] == pytest.approx(expected)


def test_pagerank_evidence_uses_causal_capital_path_not_shortest_path():
    engine = Engine({address(1): 2000 * UNIT, address(2): 1000 * UNIT + FEE}, [source(1)])
    early, _ = transfer(engine, 2, 4, 1000 * UNIT, 0, 0)
    first, _ = transfer(engine, 1, 2, 1000 * UNIT, 1, 60)
    second, _ = transfer(engine, 2, 3, 1000 * UNIT - FEE, 2, 120)
    final, result = transfer(engine, 3, 4, 1000 * UNIT - 2 * FEE, 3, 180)
    evidence = hit(result, "TAINT_PAGERANK")["evidence"]
    assert early.tx_hash not in evidence
    assert {first.tx_hash, second.tx_hash, final.tx_hash} <= set(evidence)


def test_pagerank_cannot_attribute_high_risk_capital_to_another_blacklist():
    engine = Engine({address(1): 2000 * UNIT, address(5): 2000 * UNIT}, [source(1), source(5, "high_risk")])
    transfer(engine, 1, 4, 1000 * UNIT, 0, 0)
    transfer(engine, 5, 2, 1000 * UNIT, 1, 60)
    _, result = transfer(engine, 2, 4, 950 * UNIT, 2, 120)
    assert hit(result, "TAINT_PAGERANK") is None


def test_pagerank_respects_edge_budget_before_computation(monkeypatch):
    engine = Engine({address(1): 5000 * UNIT}, [source(1)], edge_budget=1)
    transfer(engine, 1, 2, 1000 * UNIT, 0, 0)

    def forbidden(*args, **kwargs):
        pytest.fail("PageRank executed outside edge budget")

    monkeypatch.setattr(aml, "compute_pagerank", forbidden)
    _, result = transfer(engine, 1, 3, 1000 * UNIT, 1, 60)
    assert result["coverage"] == "partial"
    assert "graph_budget" in result["partial_reasons"]


def test_pagerank_nonconvergence_is_partial_not_a_failed_job(monkeypatch):
    def unavailable(*args, **kwargs):
        raise nx.PowerIterationFailedConvergence(100)

    monkeypatch.setattr(aml, "compute_pagerank", unavailable)
    engine = Engine({address(1): 2000 * UNIT}, [source(1)])
    _, result = transfer(engine, 1, 2, 1000 * UNIT, 0, 0)
    assert result["coverage"] == "partial"
    assert "pagerank_unavailable" in result["partial_reasons"]
    assert hit(result, "DIRECT_BLACKLIST") is not None


def test_pagerank_parallel_large_integer_weights_match_analytical_stationary_distribution():
    graph = nx.MultiDiGraph()
    scale = 2**60
    graph.add_edge("seed", "b", value_base=2 * scale + 1)
    graph.add_edge("seed", "b", value_base=scale - 1)
    graph.add_edge("seed", "c", value_base=scale)
    ranks = aml.compute_pagerank(graph, personalization={"seed": 1}, weight="value_base")
    expected = {"seed": 1 / 1.85, "b": 0.75 * 0.85 / 1.85, "c": 0.25 * 0.85 / 1.85}
    assert ranks == pytest.approx(expected, abs=2e-6)
    assert sum(ranks.values()) == pytest.approx(1)
    assert sum(data["value_base"] for _, _, data in graph.edges(data=True)) == 4 * scale
    assert all(isinstance(data["value_base"], int) for _, _, data in graph.edges(data=True))
