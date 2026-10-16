import hashlib
from datetime import timedelta
import pytest
from app.aml import Engine, Part, apportion
from app.domain import (
    FEE,
    GENESIS,
    THRESHOLD,
    UNIT,
    Ledger,
    SimulationConfig,
    Tx,
    canonical_hash,
    merkle,
    money,
    score,
)
from app.generator import generate


def addr(i):
    return f"sim_{i:016x}"


def tx(engine, source, target, value, index, seconds=0, height=None, tx_index=0):
    return Tx(
        canonical_hash([source, target, value, index]),
        source,
        target,
        value,
        FEE,
        engine.ledger.nonces.get(source, 0),
        GENESIS + timedelta(seconds=seconds),
        index if height is None else height,
        tx_index,
        index,
    )


def codes(result):
    return {h["code"] for h in result["hits"]}


def feed(address, **overrides):
    return {
        "network_id": "sim-account-1",
        "address": address,
        "category": "blacklist",
        "confidence": 1.0,
        "severity": 95,
        "source_name": "test TI",
        "source_ref": "synthetic://fixture",
        "list_version": "fixture-v1",
        "known_at": GENESIS.isoformat(),
        "valid_from": GENESIS.isoformat(),
        "valid_to": None,
        "enabled": True,
        **overrides,
    }


def test_T01_normal_p2p():
    e = Engine({addr(1): 1000 * UNIT}, [])
    result = e.process(tx(e, addr(1), addr(2), 10 * UNIT, 0))
    assert codes(result) == set() and result["score"] < 30


def test_T02_structuring_evidence_and_floor():
    e = Engine({addr(1): 20000 * UNIT}, [])
    hashes = []
    for i in range(8):
        t = tx(e, addr(1), addr(2), 900 * UNIT, i, i * 120)
        hashes.append(t.tx_hash)
        result = e.process(t)
    assert "STRUCTURING" in codes(result) and result["score"] >= 70
    assert next(h for h in result["hits"] if h["code"] == "STRUCTURING")["evidence"] == hashes


@pytest.mark.parametrize(
    "value,expected", [(THRESHOLD - 1, True), (THRESHOLD, False), (THRESHOLD + 1, False)]
)
def test_T03_amount_boundary(value, expected):
    e = Engine({addr(1): 20000 * UNIT}, [])
    for i in range(8):
        result = e.process(tx(e, addr(1), addr(2), value, i, i * 12))
    assert ("STRUCTURING" in codes(result)) is expected


def test_T03_inclusive_window_and_expiration():
    e = Engine({addr(1): 20000 * UNIT}, [])
    for i, s in enumerate([0, 300, 600, 900, 1200, 1500, 1700, 1800]):
        result = e.process(tx(e, addr(1), addr(2), 900 * UNIT, i, s))
    assert "STRUCTURING" in codes(result)
    assert "STRUCTURING" not in codes(e.process(tx(e, addr(1), addr(2), UNIT, 8, 1801)))


@pytest.mark.parametrize("pattern", ["SMURFING", "PEELING", "MIXING_PATTERN", "RAPID_HIGH_RISK", "CIRCULAR_FLOW", "U_TURN_BOUNCE", "TAINT_PAGERANK"])
def test_T04_T05_T07_T11_synthetic_patterns(pattern):
    dataset = generate(SimulationConfig(wallet_count=80, transaction_count=150, patterns=[pattern]))
    engine = Engine(dataset["genesis"], dataset["feed"])
    matched = []
    for t in dataset["transactions"]:
        result = engine.process(t)
        if pattern in codes(result):
            matched.append((t, result))
    assert matched, f"No {pattern} detection"
    floor = 80 if pattern == "RAPID_HIGH_RISK" else 70
    assert all(r["score"] >= floor for _, r in matched)
    if pattern == "SMURFING":
        h = next(h for h in matched[-1][1]["hits"] if h["code"] == pattern)
        assert h["observed"]["intermediates"] == 12 and h["observed"]["ratio"] <= 1
    if pattern == "PEELING":
        assert next(h for h in matched[0][1]["hits"] if h["code"] == pattern)["observed"]["levels"] == 4
    if pattern == "MIXING_PATTERN":
        o = next(h for h in matched[-1][1]["hits"] if h["code"] == pattern)["observed"]
        assert int(o["balance_start_base"]) + int(o["inflow_base"]) == int(o["balance_end_base"]) + int(
            o["outflow_base"]
        ) + int(o["fee_base"])
        assert int(o["balance_start_base"]) == 0
    if pattern == "CIRCULAR_FLOW":
        h = next(h for h in matched[-1][1]["hits"] if h["code"] == pattern)
        assert h["observed"]["hops"] == 4
        assert h["observed"]["retention_ratio"] >= 0.7
    if pattern == "U_TURN_BOUNCE":
        h = next(h for h in matched[-1][1]["hits"] if h["code"] == pattern)
        assert h["observed"]["bounce_ratio"] >= 0.9
        assert h["observed"]["dwell_seconds"] <= 300
    if pattern == "TAINT_PAGERANK":
        h = next(h for h in matched[-1][1]["hits"] if h["code"] == pattern)
        assert 0.0 <= h["observed"]["exposure_score"] <= 1.0
        assert h["observed"]["exposure_score"] >= 0.05


def test_circular_flow_unit_and_boundaries():
    genesis = {addr(1): 20000 * UNIT}
    # 1. Valid 4-hop cycle: 1 -> 2 -> 3 -> 4 -> 1
    e = Engine(genesis, [])
    t1 = tx(e, addr(1), addr(2), 1000 * UNIT, 0, 0)
    e.process(t1)
    t2 = tx(e, addr(2), addr(3), 1000 * UNIT - FEE, 1, 60)
    e.process(t2)
    t3 = tx(e, addr(3), addr(4), 1000 * UNIT - 2 * FEE, 2, 120)
    e.process(t3)
    t4 = tx(e, addr(4), addr(1), 1000 * UNIT - 3 * FEE, 3, 180)
    res = e.process(t4)
    assert "CIRCULAR_FLOW" in codes(res)
    hit = next(h for h in res["hits"] if h["code"] == "CIRCULAR_FLOW")
    assert hit["anchor"] == addr(1)
    assert hit["observed"]["hops"] == 4
    assert res["score"] >= 70

    # 2. Open path (not returning to origin: 1 -> 2 -> 3 -> 4 -> 5): no CIRCULAR_FLOW
    e2 = Engine(genesis, [])
    e2.process(tx(e2, addr(1), addr(2), 1000 * UNIT, 0, 0))
    e2.process(tx(e2, addr(2), addr(3), 1000 * UNIT - FEE, 1, 60))
    e2.process(tx(e2, addr(3), addr(4), 1000 * UNIT - 2 * FEE, 2, 120))
    res2 = e2.process(tx(e2, addr(4), addr(5), 1000 * UNIT - 3 * FEE, 3, 180))
    assert "CIRCULAR_FLOW" not in codes(res2)

    # 3. Window expired (> 3600s: 1 -> 2 -> 3 -> 1 at 3700s): no CIRCULAR_FLOW
    e3 = Engine(genesis, [])
    e3.process(tx(e3, addr(1), addr(2), 1000 * UNIT, 0, 0))
    e3.process(tx(e3, addr(2), addr(3), 1000 * UNIT - FEE, 1, 60))
    res3 = e3.process(tx(e3, addr(3), addr(1), 1000 * UNIT - 2 * FEE, 2, 3700))
    assert "CIRCULAR_FLOW" not in codes(res3)

    # 4. Low retention ratio (< 70%: 1 -> 2 -> 3 -> 1 returning only 500 of 1000 UNIT): no CIRCULAR_FLOW
    e4 = Engine(genesis, [])
    e4.process(tx(e4, addr(1), addr(2), 1000 * UNIT, 0, 0))
    e4.process(tx(e4, addr(2), addr(3), 600 * UNIT, 1, 60))
    res4 = e4.process(tx(e4, addr(3), addr(1), 500 * UNIT, 2, 120))
    assert "CIRCULAR_FLOW" not in codes(res4)


def test_u_turn_bounce_unit_and_boundaries():
    genesis = {addr(1): 20000 * UNIT}
    # 1. Valid U-Turn: 1 -> 2 (1000 SIM), then 2 -> 1 (950 SIM) after 60s
    e = Engine(genesis, [])
    t1 = tx(e, addr(1), addr(2), 1000 * UNIT, 0, 0)
    e.process(t1)
    t2 = tx(e, addr(2), addr(1), 950 * UNIT, 1, 60)
    res = e.process(t2)
    assert "U_TURN_BOUNCE" in codes(res)
    hit = next(h for h in res["hits"] if h["code"] == "U_TURN_BOUNCE")
    assert hit["anchor"] == f"{addr(1)}:{addr(2)}"
    assert hit["observed"]["dwell_seconds"] == 60
    assert hit["observed"]["bounce_ratio"] == 0.95
    assert res["score"] >= 70

    # 2. Dwell expired (> 300s): 1 -> 2 at 0s, 2 -> 1 at 350s: no U_TURN_BOUNCE
    e2 = Engine(genesis, [])
    e2.process(tx(e2, addr(1), addr(2), 1000 * UNIT, 0, 0))
    res2 = e2.process(tx(e2, addr(2), addr(1), 950 * UNIT, 1, 350))
    assert "U_TURN_BOUNCE" not in codes(res2)

    # 3. Low bounce ratio (< 90%): 1 -> 2 (1000 SIM), 2 -> 1 (800 SIM): no U_TURN_BOUNCE
    e3 = Engine(genesis, [])
    e3.process(tx(e3, addr(1), addr(2), 1000 * UNIT, 0, 0))
    res3 = e3.process(tx(e3, addr(2), addr(1), 800 * UNIT, 1, 60))
    assert "U_TURN_BOUNCE" not in codes(res3)

    # 4. Mule not drained (remaining > 5%): 2 had 2000 SIM initial, after sending still has 1050 SIM: no U_TURN_BOUNCE
    genesis4 = {addr(1): 20000 * UNIT, addr(2): 2000 * UNIT}
    e4 = Engine(genesis4, [])
    e4.process(tx(e4, addr(1), addr(2), 1000 * UNIT, 0, 0))
    res4 = e4.process(tx(e4, addr(2), addr(1), 950 * UNIT, 1, 60))
    assert "U_TURN_BOUNCE" not in codes(res4)

    # 5. Different recipient: 1 -> 2, 2 -> 3 (not U-turn back to 1): no U_TURN_BOUNCE
    e5 = Engine(genesis, [])
    e5.process(tx(e5, addr(1), addr(2), 1000 * UNIT, 0, 0))
    res5 = e5.process(tx(e5, addr(2), addr(3), 950 * UNIT, 1, 60))
    assert "U_TURN_BOUNCE" not in codes(res5)


def test_taint_pagerank_unit_and_boundaries():
    blacklist_feed = [feed(addr(1))]
    genesis = {addr(1): 20000 * UNIT, addr(10): 20000 * UNIT}

    # 1. Multi-hop propagation: 1 (blacklist) -> 2 -> 3
    e = Engine(genesis, blacklist_feed)
    res_b = e.process(tx(e, addr(1), addr(2), 5000 * UNIT, 0, 0))
    assert "TAINT_PAGERANK" in codes(res_b)
    hit_b = next(h for h in res_b["hits"] if h["code"] == "TAINT_PAGERANK")
    assert hit_b["observed"]["recipient"] == addr(2)
    assert 0.8 <= hit_b["observed"]["exposure_score"] <= 1.0
    assert res_b["score"] >= 70

    res_c = e.process(tx(e, addr(2), addr(3), 4000 * UNIT, 1, 60))
    assert "TAINT_PAGERANK" in codes(res_c)
    hit_c = next(h for h in res_c["hits"] if h["code"] == "TAINT_PAGERANK")
    assert hit_c["observed"]["recipient"] == addr(3)
    assert 0.5 <= hit_c["observed"]["exposure_score"] <= hit_b["observed"]["exposure_score"]
    assert res_c["score"] >= 70

    # 2. Weighted branching: 1 -> B1 (800 SIM) and 1 -> B2 (200 SIM)
    e2 = Engine(genesis, blacklist_feed)
    res_b1 = e2.process(tx(e2, addr(1), addr(4), 800 * UNIT, 0, 0))
    res_b2 = e2.process(tx(e2, addr(1), addr(5), 200 * UNIT, 1, 12))
    assert "TAINT_PAGERANK" in codes(res_b1) and "TAINT_PAGERANK" in codes(res_b2)
    hit_b1 = next(h for h in res_b1["hits"] if h["code"] == "TAINT_PAGERANK")
    hit_b2 = next(h for h in res_b2["hits"] if h["code"] == "TAINT_PAGERANK")
    assert hit_b1["observed"]["exposure_score"] > hit_b2["observed"]["exposure_score"]

    # 3. Disconnected flow: 10 -> 11 (clean nodes, no taint reachability from 1)
    e3 = Engine(genesis, blacklist_feed)
    res_clean = e3.process(tx(e3, addr(10), addr(11), 1000 * UNIT, 0, 0))
    assert "TAINT_PAGERANK" not in codes(res_clean)

    # 4. Inactive/Future blacklist feed: no seed in graph
    future_feed = [feed(addr(1), known_at=(GENESIS + timedelta(days=2)).isoformat())]
    e4 = Engine(genesis, future_feed)
    res_future = e4.process(tx(e4, addr(1), addr(2), 1000 * UNIT, 0, 0))
    assert "TAINT_PAGERANK" not in codes(res_future)



def test_T06_peeling_needs_four_levels():
    dataset = generate(SimulationConfig(wallet_count=80, transaction_count=150, patterns=["PEELING"]))
    engine = Engine(dataset["genesis"], [])
    hashes = set(dataset["truth"]["PEELING"][:6])
    for t in dataset["transactions"]:
        if t.tx_hash in hashes:
            assert "PEELING" not in codes(engine.process(t))
        elif t.sender in {addr(1)}:
            pass
        elif t.tx_hash not in dataset["truth"]["PEELING"]:
            engine.process(t)
        else:
            break


def test_T08_payroll_not_smurfing():
    e = Engine({addr(1): 50000 * UNIT}, [])
    for i in range(12):
        result = e.process(tx(e, addr(1), addr(10 + i), 900 * UNIT, i, i * 12))
        assert "SMURFING" not in codes(result) and "MIXING_PATTERN" not in codes(result)
    # Structuring may correctly flag near-threshold payroll; no hidden suppression.
    assert "STRUCTURING" in codes(result)


def test_peeling_restarts_after_layer_hold_exceeds_ten_minutes():
    from dataclasses import replace

    data = generate(SimulationConfig(wallet_count=80, transaction_count=150, patterns=["PEELING"]))
    engine = Engine(data["genesis"], [])
    hashes = set(data["truth"]["PEELING"])
    for i, t in enumerate(t for t in data["transactions"] if t.tx_hash in hashes):
        if i >= 6:
            t = replace(t, timestamp=t.timestamp + timedelta(minutes=20))
        assert "PEELING" not in codes(engine.process(t))


def test_T09_exact_blacklist_match():
    e = Engine({addr(1): 1000 * UNIT}, [feed(addr(2))])
    r = e.process(tx(e, addr(1), addr(2), UNIT, 0))
    assert "DIRECT_BLACKLIST" in codes(r) and r["score"] >= 95
    assert r["hits"][0]["observed"]["matches"][0]["direction"] == "recipient"


@pytest.mark.parametrize(
    "source",
    [
        feed(addr(1), network_id="another-network"),
        feed(addr(1), valid_to=GENESIS.isoformat()),
        feed(addr(1), known_at=(GENESIS + timedelta(days=1)).isoformat()),
    ],
)
def test_T10_unavailable_feed(source):
    e = Engine({addr(1): 1000 * UNIT}, [source])
    assert "DIRECT_BLACKLIST" not in codes(e.process(tx(e, addr(1), addr(2), UNIT, 0)))


def test_T10_retrospective_separate_context():
    source = feed(addr(1), known_at=(GENESIS + timedelta(days=1)).isoformat())
    e = Engine({addr(1): 1000 * UNIT}, [source], mode="retrospective", as_of_time=GENESIS + timedelta(days=2))
    r = e.process(tx(e, addr(1), addr(2), UNIT, 0))
    assert "DIRECT_BLACKLIST" in codes(r) and r["analysis_mode"] == "retrospective"


def test_T12_preexisting_transfer_is_not_future_flow():
    e = Engine({addr(1): 10000 * UNIT, addr(2): 10000 * UNIT}, [feed(addr(1))])
    e.process(tx(e, addr(2), addr(3), 1000 * UNIT, 0, 0))
    e.process(tx(e, addr(1), addr(2), 1000 * UNIT, 1, 60))
    assert all(p.source is None for p in e.parts[addr(3)])


def test_T13_same_timestamp_respects_index():
    e = Engine({addr(1): 10000 * UNIT}, [])
    first = tx(e, addr(1), addr(2), UNIT, 0, height=0, tx_index=0)
    result = e.process(first)
    assert result["components"]["V"] == 0.05 and e.graph.number_of_edges() == 1
    second = tx(e, addr(1), addr(2), UNIT, 1, height=0, tx_index=1)
    e.process(second)
    assert e.graph.number_of_edges() == 2
    with pytest.raises(ValueError):
        e.process(first)


def test_T14_dust_does_not_taint_entire_balance():
    e = Engine({addr(1): 2 * UNIT, addr(2): 2_000_000 * UNIT}, [feed(addr(1))])
    assert e.process(tx(e, addr(1), addr(2), UNIT, 0))["score"] >= 95
    result = e.process(tx(e, addr(2), addr(3), 1000 * UNIT, 1, 60))
    assert result["components"]["E"] < 0.001 and result["score"] < 50


def test_rapid_uses_allocated_forward_ratio_not_total_transaction_value():
    e = Engine({addr(1): 2000 * UNIT, addr(2): 10000 * UNIT, addr(3): 10000 * UNIT}, [feed(addr(1))])
    e.process(tx(e, addr(1), addr(2), 1000 * UNIT, 0))
    e.process(tx(e, addr(2), addr(3), 1000 * UNIT, 1, 60))
    result = e.process(tx(e, addr(3), addr(4), 1000 * UNIT, 2, 120))
    assert "RAPID_HIGH_RISK" not in codes(result)


def test_T15_cycle_self_transfer_and_conservation():
    e = Engine({addr(1): 10000 * UNIT}, [feed(addr(1))])
    e.process(tx(e, addr(1), addr(2), 1000 * UNIT, 0))
    e.process(tx(e, addr(2), addr(1), 500 * UNIT, 1, 12))
    before = e.ledger.balances[addr(1)]
    e.process(tx(e, addr(1), addr(1), 100 * UNIT, 2, 24))
    assert e.ledger.balances[addr(1)] == before - FEE
    assert sum(e.ledger.balances.values()) == 10000 * UNIT
    assert all(sum(p.amount for p in portions) == e.ledger.balances[a] for a, portions in e.parts.items())


@pytest.mark.parametrize(
    "value,nonce,fee", [(0, 0, FEE), (-1, 0, FEE), (10000 * UNIT, 0, FEE), (UNIT, 1, FEE), (UNIT, 0, 0)]
)
def test_T16_rejects_invalid_intents_atomically(value, nonce, fee):
    ledger = Ledger({addr(1): 1000 * UNIT})
    before = ledger.balances.copy()
    with pytest.raises(ValueError):
        ledger.apply(addr(1), addr(2), value, nonce, fee)
    assert ledger.balances == before and ledger.nonces == {}


def test_T18_large_amount_and_parser():
    amount = 2**53 + 1
    ledger = Ledger({addr(1): amount + FEE})
    ledger.apply(addr(1), addr(2), amount, 0)
    assert ledger.balances[addr(2)] == amount and money(str(amount)) == amount
    for value in [1, True, "-1", "1.1", "1e6", "00"]:
        with pytest.raises(ValueError):
            money(value)
    with pytest.raises(ValueError):
        ledger.apply("bad", addr(2), 1, 0)
    valid = tx(Engine({addr(1): amount + FEE}, []), addr(1), addr(2), amount, 0).payload()
    for key, bad in [
        ("asset_id", "OTHER"),
        ("network_id", "other-network"),
        ("status", "reverted"),
        ("schema_version", 2),
    ]:
        with pytest.raises(ValueError):
            Tx.from_payload({**valid, key: bad})


def test_T19_score_floor_and_half_up():
    assert score(dict(E=0.8, P=1, V=0.9, Q=0.7, A=0.8), ["MIXING_PATTERN"])["score"] == 87
    assert score(dict(E=0, P=1, V=0, Q=0, A=0), ["PEELING"])["score"] == 70
    assert score(dict(E=0, P=0, V=1, Q=1, A=0), [])["score"] == 25


@pytest.mark.parametrize("limits", [{"edge_budget": 0}, {"path_budget": 0}])
def test_T20_budget_partial(limits):
    e = Engine({addr(1): 10000 * UNIT}, [], **limits)
    e.process(tx(e, addr(1), addr(2), 2 * UNIT, 0))
    r = e.process(tx(e, addr(2), addr(3), UNIT, 1, 12))
    assert r["coverage"] == "partial" and r["truncated"]
    assert {"graph_budget", "evidence_path_budget"} & set(r["partial_reasons"])


def test_T21_deterministic_hash_and_supply():
    config = SimulationConfig(wallet_count=80, transaction_count=150)
    a, b = generate(config), generate(config)
    assert a["ledger_hash"] == b["ledger_hash"] and a["transactions"] == b["transactions"]
    assert sum(a["genesis"].values()) == sum(a["final_balances"].values())
    assert canonical_hash({"b": 2, "a": "1"}) == hashlib.sha256(b'{"a":"1","b":2}').hexdigest()
    assert merkle([]) == hashlib.sha256(b"").hexdigest()


def test_T22_hidden_metadata_cannot_change_predictions():
    data = generate(SimulationConfig(wallet_count=80, transaction_count=150))
    engines = [Engine(data["genesis"], data["feed"]), Engine(data["genesis"], data["feed"])]
    for t in data["transactions"]:
        hidden = {
            **t.payload(),
            "scenario_id": "changed",
            "actor_id": "secret",
            "ground_truth_role": "malicious",
        }
        assert engines[0].process(t) == engines[1].process(Tx.from_payload(hidden))


def test_full_spend_rounding_keeps_integer_mass():
    parts = [Part(1, addr(1)), Part(2, addr(2)), Part(4, addr(3))]
    sent, kept = apportion(parts, 7)
    assert sum(p.amount for p in sent) == 7 and kept == []
    sent, kept = apportion(parts, 3)
    assert sum(p.amount for p in sent) == 3 and sum(p.amount for p in kept) == 4
