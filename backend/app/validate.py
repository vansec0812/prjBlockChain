"""Reproducibility checks across seeds. This is not a held-out quality benchmark."""

import argparse
import json
import platform
import sys
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from app.aml import Engine
from app.domain import GENERATOR_VERSION, RULE_VERSION, Ledger, SimulationConfig, canonical_hash
from app.generator import generate


def validate_seed(config: SimulationConfig) -> dict:
    start = perf_counter()
    first, repeat = generate(config), generate(config)
    if first["ledger_hash"] != repeat["ledger_hash"] or first["transactions"] != repeat["transactions"]:
        raise AssertionError("Generator is not deterministic")
    ledger = Ledger(first["genesis"])
    for tx in first["transactions"]:
        ledger.apply(tx.sender, tx.recipient, tx.value, tx.nonce, tx.fee)
    if ledger.balances != first["final_balances"]:
        raise AssertionError("Replay balances differ from generation")
    predictions = []
    for dataset in (first, repeat):
        engine = Engine(dataset["genesis"], dataset["feed"])
        predictions.append([engine.process(tx) for tx in dataset["transactions"]])
    if canonical_hash(predictions[0]) != canonical_hash(predictions[1]):
        raise AssertionError("Analysis is not deterministic")
    return {
        "seed": config.seed,
        "config": config.model_dump(),
        "ledger_hash": first["ledger_hash"],
        "predictions_hash": canonical_hash(predictions[0]),
        "transaction_count": len(first["transactions"]),
        "genesis_supply_base": str(sum(first["genesis"].values())),
        "final_supply_base": str(sum(ledger.balances.values())),
        "fee_sink_base": str(ledger.balances["fee_sink"]),
        "coverage_counts": dict(Counter(p["coverage"] for p in predictions[0])),
        "rule_hit_counts": dict(
            sorted(Counter(h["code"] for p in predictions[0] for h in p["hits"]).items())
        ),
        "validation_seconds": perf_counter() - start,
        "checks": ["generator repeat", "ledger replay", "supply conservation", "analysis repeat"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", default="42,43,44,45,46")
    parser.add_argument("--transactions", type=int, default=5000)
    parser.add_argument("--wallets", type=int, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        seeds = [int(value) for value in args.seeds.split(",")]
        if not seeds or len(seeds) > 20 or len(set(seeds)) != len(seeds):
            raise ValueError("Use 1–20 unique seeds")
        configs = [
            SimulationConfig(
                seed=s, wallet_count=args.wallets, transaction_count=args.transactions
            ).checked_patterns()
            for s in seeds
        ]
    except ValueError as exc:
        parser.error(str(exc))
    source = {p.name: p.read_text(encoding="utf-8") for p in sorted(Path(__file__).parent.glob("*.py"))}
    report = {
        "schema_version": 1,
        "rule_version": RULE_VERSION,
        "generator_version": GENERATOR_VERSION,
        "scope": "multi-seed repeatability and ledger validation; no held-out split, tuning or quality benchmark",
        "source_hash": canonical_hash(source),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "networkx": version("networkx"),
        "runs": [validate_seed(c) for c in configs],
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
