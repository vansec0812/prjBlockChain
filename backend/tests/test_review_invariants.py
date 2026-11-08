"""Independent rejection and history counterexamples from the source audit."""

import pickle
from dataclasses import replace

import pytest

from app.aml import Engine
from app.domain import FEE, GENESIS, UNIT, Ledger, Tx, canonical_hash


def address(i):
    return f"sim_{i:016x}"


def transaction(**overrides):
    value = Tx(canonical_hash(["review", 0]), address(1), address(2), UNIT, FEE, 0, GENESIS, 0, 0, 0)
    return replace(value, **overrides)


@pytest.mark.parametrize("changes", [{"nonce": 1}, {"fee": 1}, {"value": -1}, {"recipient": "invalid"}])
def test_rejected_intent_does_not_mutate_any_engine_state(changes):
    engine = Engine({address(1): 10 * UNIT}, [])
    before = pickle.dumps(engine.__dict__)
    with pytest.raises(ValueError):
        engine.process(transaction(**changes))
    assert pickle.dumps(engine.__dict__) == before
    assert engine.process(transaction())["as_of_order"] == [0, 0]


@pytest.mark.parametrize("target", [address(2), "fee_sink"])
def test_balance_overflow_rejected_atomically(target):
    ledger = Ledger({address(1): 10 * UNIT, target: 10**78 - 1})
    before = pickle.dumps(ledger)
    with pytest.raises(ValueError):
        ledger.apply(address(1), address(2), UNIT, 0)
    assert pickle.dumps(ledger) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"timestamp": "2026-09-08T00:00:00"},
        {"block_height": -1},
        {"tx_index": True},
        {"global_index": -1},
        {"nonce": True},
        {"from_address": "fee_sink"},
        {"tx_hash": "bad"},
        {"schema_version": True},
    ],
)
def test_transaction_contract_rejects_invalid_metadata(changes):
    with pytest.raises(ValueError):
        Tx.from_payload({**transaction().payload(), **changes})
