"""Seeded generator. Campaign labels leave through a separate evaluation channel."""

import random
from collections import defaultdict
from dataclasses import replace
from datetime import timedelta
from app.domain import FEE, GENESIS, UNIT, Ledger, SimulationConfig, Tx, canonical_hash, merkle


def generate(config: SimulationConfig, on_progress=None):
    config.checked_patterns()
    rng = random.Random(config.seed)
    addresses = [
        "sim_" + canonical_hash(["address-v1", config.seed, i])[:16] for i in range(config.wallet_count)
    ]
    genesis = {a: (1_000_000 * UNIT if i >= 64 else 0) for i, a in enumerate(addresses)}
    for i in [0, 2, 16, 52, 57]:
        genesis[addresses[i]] = 20_000 * UNIT
    for i in range(29, 37):
        genesis[addresses[i]] = 3_001 * UNIT
    intents = []
    seq = 0

    def add(seconds, source, target, value, label=None):
        nonlocal seq
        intents.append((seconds, seq, addresses[source], addresses[target], value, label))
        seq += 1

    if "STRUCTURING" in config.patterns:
        for i in range(12):
            add(i * 120, 0, 1, rng.randint(800, 990) * UNIT, "STRUCTURING")
    if "SMURFING" in config.patterns:
        for i in range(12):
            add(2100 + i * 24, 2, 4 + i, 900 * UNIT, "SMURFING")
            add(2500 + i * 24, 4 + i, 3, 900 * UNIT - FEE, "SMURFING")
    if "PEELING" in config.patterns:
        balance = genesis[addresses[16]]
        for i in range(6):
            peel = balance * 8 // 100
            continuation = balance - peel - 2 * FEE
            add(4200 + i * 120, 16 + i, 23 + i, peel, "PEELING")
            add(4212 + i * 120, 16 + i, 17 + i, continuation, "PEELING")
            balance = continuation
    if "MIXING_PATTERN" in config.patterns:
        for i in range(8):
            for j in range(3):
                add(5400 + (i * 3 + j) * 12, 29 + i, 37 + j, 1000 * UNIT, "MIXING_PATTERN")
        for i in range(6):
            add(5700 + i * 12, 37 + i % 3, 37 + (i + 1) % 3, 1500 * UNIT, "MIXING_PATTERN")
        for i in range(12):
            add(5820 + i * 12, 37 + i % 3, 40 + i, 1800 * UNIT, "MIXING_PATTERN")
    if "RAPID_HIGH_RISK" in config.patterns:
        value = 18_000 * UNIT
        for i in range(5):
            add(7200 + i * 60, 52 + i, 53 + i, value, "RAPID_HIGH_RISK")
            value -= FEE
    if "CIRCULAR_FLOW" in config.patterns:
        val = 15_000 * UNIT
        add(8400, 57, 58, val, "CIRCULAR_FLOW")
        val -= FEE
        add(8460, 58, 59, val, "CIRCULAR_FLOW")
        val -= FEE
        add(8520, 59, 60, val, "CIRCULAR_FLOW")
        val -= FEE
        add(8580, 60, 57, val, "CIRCULAR_FLOW")
    for _ in range(config.transaction_count - len(intents)):
        a, b = rng.sample(range(64, config.wallet_count), 2)
        add(rng.randint(0, config.simulation_days * 86400), a, b, rng.randint(10, 200) * UNIT)
    intents.sort(key=lambda x: (x[0], x[1]))
    ledger = Ledger(genesis)
    blocks = []
    labels = defaultdict(list)
    pending = []
    current_slot = None
    all_tx = []
    touched = set()

    def flush():
        nonlocal pending, touched
        if not pending:
            return
        root = merkle([t.tx_hash for t in pending])
        header = {
            "parent_hash": blocks[-1]["block_hash"] if blocks else None,
            "height": len(blocks),
            "timestamp": pending[0].timestamp.isoformat(),
            "tx_root": root,
            "state_checksum": ledger.checksum(),
        }
        block_hash = canonical_hash(header)
        txs = [replace(t, block_hash=block_hash) for t in pending]
        blocks.append(
            {
                **header,
                "block_hash": block_hash,
                "txs": txs,
                "tx_count": len(txs),
                "gas_used": 21000 * len(txs),
                "total_fees_base": str(FEE * len(txs)),
                "finality_mode": "simulated_final",
                "states": {a: (ledger.balances[a], ledger.nonces.get(a, 0)) for a in touched},
            }
        )
        all_tx.extend(txs)
        pending, touched = [], set()

    for seconds, _, sender, recipient, value, label in intents:
        slot = seconds // config.block_interval_seconds
        if current_slot != slot or len(pending) >= config.max_transactions_per_block:
            flush()
        current_slot = slot
        timestamp = GENESIS + timedelta(seconds=slot * config.block_interval_seconds)
        nonce = ledger.nonces.get(sender, 0)
        intent = {
            "network_id": "sim-account-1",
            "sender": sender,
            "recipient": recipient,
            "value_base": str(value),
            "nonce": nonce,
            "gas_limit": 21000,
            "gas_used": 21000,
            "effective_gas_price_base": "1",
        }
        tx_hash = canonical_hash(intent)
        ledger.apply(sender, recipient, value, nonce)
        tx = Tx(
            tx_hash,
            sender,
            recipient,
            value,
            FEE,
            nonce,
            timestamp,
            len(blocks),
            len(pending),
            len(all_tx) + len(pending),
        )
        pending.append(tx)
        if on_progress and (len(all_tx) + len(pending)) % 100 == 0:
            on_progress(len(all_tx) + len(pending))
        touched.update([sender, recipient, "fee_sink"])
        if label:
            labels[label].append(tx_hash)
    flush()
    feed = []
    if "RAPID_HIGH_RISK" in config.patterns:
        feed.append(
            {
                "network_id": "sim-account-1",
                "address": addresses[52],
                "category": "blacklist",
                "severity": 95,
                "confidence": 1.0,
                "source_name": "Synthetic TI",
                "source_ref": "synthetic://ti-v1",
                "known_at": GENESIS.isoformat(),
                "valid_from": GENESIS.isoformat(),
                "valid_to": None,
                "list_version": f"synthetic-v1-{config.seed}",
                "enabled": True,
            }
        )
    return {
        "genesis": genesis,
        "blocks": blocks,
        "transactions": all_tx,
        "truth": dict(labels),
        "feed": feed,
        "final_balances": ledger.balances,
        "ledger_hash": blocks[-1]["block_hash"],
    }
