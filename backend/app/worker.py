"""Durable local worker, one consumer under a PostgreSQL advisory lock."""

import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from functools import lru_cache
from uuid import NAMESPACE_URL, uuid5
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker
from app.aml import Engine
from app.db import Session, engine, make_engine
from app.domain import SimulationConfig, Tx, canonical_hash, score
from app.evaluation import evaluate
from app.generator import generate
from app.models import (
    Alert,
    AlertSnapshot,
    AlertTransaction,
    Assessment,
    Block,
    Campaign,
    Checkpoint,
    Event,
    Feed,
    Job,
    Metric,
    Outbox,
    RuleHit,
    Run,
    Transaction,
    Wallet,
    now,
)

log = logging.getLogger("aml.worker")
HEARTBEAT = Path("/tmp/aml-worker-heartbeat")
stopping = False


def heartbeat():
    HEARTBEAT.write_text(str(time.time()))


def identity(run_id: str, value: str) -> str:
    return str(uuid5(NAMESPACE_URL, run_id + ":" + value))


def upsert(db, model, values: list[dict], keys: list[str], updates: list[str]):
    if not values:
        return
    statement = insert(model).values(values)
    if updates:
        statement = statement.on_conflict_do_update(
            index_elements=keys, set_={k: getattr(statement.excluded, k) for k in updates}
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=keys)
    db.execute(statement)


@lru_cache(maxsize=1)
def evaluator_session():
    return sessionmaker(make_engine(os.environ["EVALUATOR_DATABASE_URL"]), expire_on_commit=False)


def same_episode(payload: dict, hit: dict, tx: Tx, quiet_seconds: int = 1800) -> bool:
    previous = datetime.fromisoformat(payload.get("latest_assessment", payload["assessment"])["detected_at"])
    if not 0 <= (tx.timestamp - previous).total_seconds() <= quiet_seconds:
        return False
    if hit["code"] == "MIXING_PATTERN":
        old = set(payload["observed"].get("region", []))
        current = set(hit["observed"].get("region", []))
        return bool(old and current and len(old & current) * 2 >= min(len(old), len(current)))
    return payload["anchor"] == hit["anchor"]


def generate_ledger(job_id: str):
    with Session() as db:
        job = db.get(Job, job_id)
        run = db.get(Run, job.run_id)
        config = SimulationConfig(**run.config)
        run_id, version = run.id, job.analysis_version

    def generation_pulse(_):
        heartbeat()
        if stopping:
            raise InterruptedError("Worker shutdown during deterministic generation")

    dataset = generate(config, on_progress=generation_pulse)
    with Session.begin() as db:
        run = db.get(Run, run_id)
        run.genesis = {a: str(v) for a, v in dataset["genesis"].items()}
        run.genesis["fee_sink"] = "0"
        existing_feed = [f.payload for f in db.scalars(select(Feed))]
        for f in dataset["feed"]:
            upsert(
                db,
                Feed,
                [
                    {
                        "id": identity(run_id, f["address"] + f["list_version"]),
                        "network_id": f["network_id"],
                        "address": f["address"],
                        "known_at": datetime.fromisoformat(f["known_at"]),
                        "source_name": f["source_name"],
                        "list_version": f["list_version"],
                        "payload": f,
                    }
                ],
                ["network_id", "address", "source_name", "list_version"],
                [],
            )
        job = db.get(Job, job_id)
        # Preserve the snapshot across retries; never silently pick a later feed.
        if "feed" not in job.config:
            snapshot = {
                (f["network_id"], f["address"], f["source_name"], f["list_version"]): f
                for f in existing_feed + dataset["feed"]
            }
            job.config = {**job.config, "feed": [snapshot[k] for k in sorted(snapshot)], "mode": "as-known"}
        run.feed_snapshot = job.config["feed"]
        upsert(
            db,
            Wallet,
            [
                {
                    "run_id": run_id,
                    "id": identity(run_id, a),
                    "address": a,
                    "balance_base": int(v),
                    "next_nonce": 0,
                    "is_system": a == "fee_sink",
                }
                for a, v in run.genesis.items()
            ],
            ["run_id", "id"],
            [],
        )
    EvalSession = evaluator_session()
    with EvalSession.begin() as ev:
        upsert(
            ev,
            Campaign,
            [
                {"run_id": run_id, "id": pattern, "pattern": pattern, "tx_hashes": hashes}
                for pattern, hashes in dataset["truth"].items()
            ],
            ["run_id", "id"],
            [],
        )
    with Session() as db:
        persisted = {b.height: b.block_hash for b in db.scalars(select(Block).where(Block.run_id == run_id))}
    for chunk_start in range(0, len(dataset["blocks"]), 100):
        if stopping:
            raise InterruptedError("Worker shutdown at a safe block boundary")
        with Session.begin() as db:
            for block in dataset["blocks"][chunk_start : chunk_start + 100]:
                if block["height"] in persisted:
                    if persisted[block["height"]] != block["block_hash"]:
                        raise ValueError("Persisted ledger hash mismatch")
                    continue
                block_id = identity(run_id, block["block_hash"])
                payload = {k: v for k, v in block.items() if k not in {"txs", "states"}}
                db.add(
                    Block(
                        run_id=run_id,
                        id=block_id,
                        height=block["height"],
                        block_hash=block["block_hash"],
                        parent_id=identity(run_id, block["parent_hash"]) if block["parent_hash"] else None,
                        timestamp=datetime.fromisoformat(block["timestamp"]),
                        payload=payload,
                    )
                )
                db.flush()
                tx_values, work_values = [], []
                for tx in block["txs"]:
                    payload = {**tx.payload(), "run_id": run_id, "observed_at": now().isoformat()}
                    tx_values.append(
                        {
                            "run_id": run_id,
                            "id": identity(run_id, tx.tx_hash),
                            "tx_hash": tx.tx_hash,
                            "block_id": block_id,
                            "global_index": tx.global_index,
                            "block_height": tx.height,
                            "tx_index": tx.index,
                            "from_wallet_id": identity(run_id, tx.sender),
                            "to_wallet_id": identity(run_id, tx.recipient),
                            "from_address": tx.sender,
                            "to_address": tx.recipient,
                            "value_base": tx.value,
                            "fee_base": tx.fee,
                            "nonce": tx.nonce,
                            "timestamp": tx.timestamp,
                            "payload": payload,
                        }
                    )
                    work_values.append(
                        {
                            "run_id": run_id,
                            "tx_hash": tx.tx_hash,
                            "analysis_version": version,
                            "global_index": tx.global_index,
                            "processed": False,
                        }
                    )
                upsert(db, Transaction, tx_values, ["run_id", "id"], [])
                upsert(db, Outbox, work_values, ["run_id", "tx_hash", "analysis_version"], [])
                upsert(
                    db,
                    Wallet,
                    [
                        {
                            "run_id": run_id,
                            "id": identity(run_id, a),
                            "address": a,
                            "balance_base": balance,
                            "next_nonce": nonce,
                            "is_system": a == "fee_sink",
                        }
                        for a, (balance, nonce) in block["states"].items()
                    ],
                    ["run_id", "id"],
                    ["balance_base", "next_nonce"],
                )
            run = db.get(Run, run_id)
            run.tx_count = (
                db.scalar(
                    select(Transaction.global_index)
                    .where(Transaction.run_id == run_id)
                    .order_by(Transaction.global_index.desc())
                    .limit(1)
                )
                + 1
            )
            db.add(
                Event(
                    run_id=run_id,
                    kind="progress",
                    payload={"phase": "ledger", "count": run.tx_count, "total": config.transaction_count},
                )
            )
        heartbeat()
    return dataset


def analyze(job_id: str):
    with Session() as db:
        job = db.get(Job, job_id)
        run = db.get(Run, job.run_id)
        run_id, version, config = run.id, job.analysis_version, job.config
        genesis = {a: int(v) for a, v in run.genesis.items()}
        transactions = list(
            db.scalars(
                select(Transaction).where(Transaction.run_id == run_id).order_by(Transaction.global_index)
            )
        )
        checkpoint = db.get(Checkpoint, (run_id, version))
        saved_index = checkpoint.global_index if checkpoint else -1
    analyzer = Engine(
        genesis,
        config["feed"],
        mode=config.get("mode", "as-known"),
        as_of_time=datetime.fromisoformat(config["as_of_time"]) if config.get("as_of_time") else None,
        run_id=run_id,
    )
    online = []
    timings = []
    start = time.perf_counter()
    # Rebuilding from genesis before the checkpoint restores RAM allocation state exactly.
    with Session() as db:
        for chunk_start in range(0, len(transactions), 100):
            if stopping:
                raise InterruptedError("Worker shutdown at a safe analysis boundary")
            chunk = transactions[chunk_start : chunk_start + 100]
            for row in chunk:
                tx = Tx.from_payload(row.payload)
                t0 = time.perf_counter()
                result = analyzer.process(tx)
                timings.append(time.perf_counter() - t0)
                online.append((tx.tx_hash, result))
                if tx.global_index <= saved_index:
                    continue
                db.add(
                    Assessment(
                        run_id=run_id,
                        tx_hash=tx.tx_hash,
                        analysis_version=version,
                        as_of_order=tx.global_index,
                        score=result["score"],
                        payload={**result, "analysis_version": version},
                    )
                )
                db.flush()
                for hit in result["hits"]:
                    recent_alerts = db.scalars(
                        select(Alert)
                        .where(
                            Alert.run_id == run_id,
                            Alert.analysis_version == version,
                            Alert.detector_code == hit["code"],
                        )
                        .order_by(Alert.last_detected_at.desc())
                        .limit(100)
                    )
                    alert = next((a for a in recent_alerts if same_episode(a.payload, hit, tx)), None)
                    key = canonical_hash([run_id, version, hit["code"], hit["anchor"], tx.global_index])
                    if alert is None:
                        alert = Alert(
                            run_id=run_id,
                            id=identity(run_id, key),
                            trigger_tx_hash=tx.tx_hash,
                            detector_code=hit["code"],
                            anchor=hit["anchor"],
                            analysis_version=version,
                            dedup_key=key,
                            score=result["score"],
                            severity=result["severity"],
                            detected_order=tx.global_index,
                            detected_at=now(),
                            last_detected_at=now(),
                            payload={
                                **hit,
                                "assessment": result,
                                "coverage": result["coverage"],
                                "last_detected_order": tx.global_index,
                            },
                        )
                        db.add(alert)
                        db.flush()
                        db.add(
                            Event(
                                run_id=run_id,
                                kind="alert",
                                payload={"alert_id": alert.id, "analysis_version": version},
                            )
                        )
                    else:
                        alert.last_detected_at = now()
                        alert.revision += 1
                        peak = alert.payload["assessment"]
                        if result["score"] >= alert.score:
                            alert.score, alert.severity = result["score"], result["severity"]
                            peak = result
                        alert.payload = {
                            **alert.payload,
                            "last_detected_order": tx.global_index,
                            "assessment": peak,
                            "latest_assessment": result,
                            "evidence": list(dict.fromkeys(alert.payload["evidence"] + hit["evidence"])),
                            "observed": hit["observed"],
                        }
                    db.flush()
                    snapshot = {
                        **alert.payload,
                        "score": alert.score,
                        "severity": alert.severity,
                        "trigger_tx_hash": alert.trigger_tx_hash,
                        "detected_order": alert.detected_order,
                    }
                    upsert(
                        db,
                        AlertSnapshot,
                        [
                            {
                                "run_id": run_id,
                                "alert_id": alert.id,
                                "as_of_order": tx.global_index,
                                "payload": snapshot,
                            }
                        ],
                        ["run_id", "alert_id", "as_of_order"],
                        [],
                    )
                    upsert(
                        db,
                        RuleHit,
                        [{"run_id": run_id, "alert_id": alert.id, "rule_code": hit["code"], "payload": hit}],
                        ["run_id", "alert_id", "rule_code"],
                        ["payload"],
                    )
                    upsert(
                        db,
                        AlertTransaction,
                        [
                            {"run_id": run_id, "alert_id": alert.id, "tx_hash": h, "path_order": i}
                            for i, h in enumerate(hit["evidence"])
                        ],
                        ["run_id", "alert_id", "tx_hash"],
                        [],
                    )
                    # Preserve online assessment; append a separately identified retrospective revision.
                    for h in hit["evidence"]:
                        if h == tx.tx_hash:
                            continue
                        old = db.scalar(
                            select(Assessment)
                            .where(
                                Assessment.run_id == run_id,
                                Assessment.tx_hash == h,
                                Assessment.analysis_version == version,
                            )
                            .order_by(Assessment.as_of_order)
                            .limit(1)
                        )
                        if old is None:
                            continue
                        components = {**old.payload["components"]}
                        codes = [x["code"] for x in old.payload["hits"]] + [hit["code"]]
                        if hit["code"] in {"STRUCTURING", "SMURFING", "PEELING", "MIXING_PATTERN", "CIRCULAR_FLOW", "U_TURN_BOUNCE", "TAINT_PAGERANK"}:
                            components["P"] = 1.0
                        revision = {
                            **old.payload,
                            **score(components, codes),
                            "hits": old.payload["hits"] + [hit],
                            "analysis_mode": "retrospective",
                            "retrospective_reason": "motif_completed",
                            "as_of_order": list(tx.order),
                            "detected_at": tx.timestamp.isoformat(),
                        }
                        upsert(
                            db,
                            Assessment,
                            [
                                {
                                    "run_id": run_id,
                                    "tx_hash": h,
                                    "analysis_version": version,
                                    "as_of_order": tx.global_index,
                                    "score": revision["score"],
                                    "payload": revision,
                                }
                            ],
                            ["run_id", "tx_hash", "analysis_version", "as_of_order"],
                            ["score", "payload"],
                        )
            last = chunk[-1].global_index
            db.execute(
                update(Outbox)
                .where(
                    Outbox.run_id == run_id, Outbox.analysis_version == version, Outbox.global_index <= last
                )
                .values(processed=True)
            )
            upsert(
                db,
                Checkpoint,
                [{"run_id": run_id, "analysis_version": version, "global_index": last, "updated_at": now()}],
                ["run_id", "analysis_version"],
                ["global_index", "updated_at"],
            )
            run = db.get(Run, run_id)
            run.analyzed_count = last + 1
            db.add(
                Event(
                    run_id=run_id,
                    kind="progress",
                    payload={
                        "phase": "analysis",
                        "count": last + 1,
                        "total": len(transactions),
                        "analysis_version": version,
                    },
                )
            )
            db.commit()
            heartbeat()
    elapsed = time.perf_counter() - start
    EvalSession = evaluator_session()
    with EvalSession() as ev:
        truth = {
            c.pattern: c.tx_hashes for c in ev.scalars(select(Campaign).where(Campaign.run_id == run_id))
        }
    with Session.begin() as db:
        alerts = [
            a.payload
            for a in db.scalars(
                select(Alert).where(Alert.run_id == run_id, Alert.analysis_version == version)
            )
        ]
        metrics = evaluate(truth, online, alerts)
        mode = config.get("mode", "as-known")
        metrics["analysis_mode"] = mode
        if mode == "retrospective":
            metrics["sequential_with_retrospective_feed"] = metrics.pop("online")
            for pattern in metrics["patterns"].values():
                pattern["sequential_with_retrospective_feed"] = pattern.pop("online")
            metrics["scope"] = "single synthetic run with retrospective TI; not historical online evaluation"
        timings.sort()
        metrics.update(
            {
                "analysis_version": version,
                "analysis_seconds": elapsed,
                "throughput_tx_s": len(transactions) / elapsed,
                "process_p50_ms": timings[len(timings) // 2] * 1000,
                "process_p95_ms": timings[int(len(timings) * 0.95)] * 1000,
                "measurement": "worker wall time including persistence; per-tx timing is engine computation only",
                "partial_assessments": sum(a["coverage"] == "partial" for _, a in online),
                "baseline": "B2",
                "model_status": "not_configured",
                "seed": run.seed,
                "rule_version": "rules-v1",
                "feed_versions": sorted({f["list_version"] for f in config["feed"]}),
                "ledger_hash": db.scalar(
                    select(Block.block_hash)
                    .where(Block.run_id == run_id)
                    .order_by(Block.height.desc())
                    .limit(1)
                ),
            }
        )
        upsert(
            db,
            Metric,
            [{"run_id": run_id, "analysis_version": version, "payload": metrics}],
            ["run_id", "analysis_version"],
            ["payload"],
        )


def handle(job_id: str):
    with Session.begin() as db:
        job = db.get(Job, job_id)
        job.status = "running"
        run = db.get(Run, job.run_id)
        run.status = "running"
        kind, run_id = job.kind, job.run_id
        db.add(Event(run_id=run_id, kind="job", payload={"job_id": job_id, "status": "running"}))
    try:
        if kind == "generate":
            generate_ledger(job_id)
        analyze(job_id)
        with Session.begin() as db:
            job = db.get(Job, job_id)
            job.status, job.finished_at = "completed", now()
            run = db.get(Run, run_id)
            run.status, run.error = "completed", None
            db.add(Event(run_id=run_id, kind="job", payload={"job_id": job_id, "status": "completed"}))
            maximum = db.scalar(select(func.max(Event.id))) or 0
            db.execute(delete(Event).where(Event.id < maximum - 10000))
    except InterruptedError:
        with Session.begin() as db:
            db.get(Job, job_id).status = "queued"
    except Exception as exc:
        log.exception("job_failed run=%s job=%s type=%s", run_id, job_id, type(exc).__name__)
        with Session.begin() as db:
            job = db.get(Job, job_id)
            job.status, job.error = "failed", f"{type(exc).__name__}: xử lý thất bại; xem worker logs."
            run = db.get(Run, run_id)
            run.status, run.error = "failed", job.error
            db.add(Event(run_id=run_id, kind="job", payload={"job_id": job_id, "status": "failed"}))


def main():
    global stopping

    def stop(*_):
        global stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.basicConfig(level=logging.INFO)
    with engine.connect() as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(420801)")):
            raise RuntimeError("Another local worker owns the lock")
        lock.commit()
        with Session.begin() as db:
            db.execute(update(Job).where(Job.status == "running").values(status="queued"))
        while not stopping:
            heartbeat()
            with Session() as db:
                job_id = db.scalar(
                    select(Job.id).where(Job.status == "queued").order_by(Job.created_at).limit(1)
                )
            if job_id:
                handle(job_id)
            else:
                time.sleep(1)


if __name__ == "__main__":
    if "--health" in sys.argv:
        sys.exit(0 if HEARTBEAT.exists() and time.time() - float(HEARTBEAT.read_text()) < 90 else 1)
    main()
