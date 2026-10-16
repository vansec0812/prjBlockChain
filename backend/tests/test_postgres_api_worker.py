"""Integration tests target only the explicitly named disposable aml_test database."""

import os
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker
from app import worker
from app.aml import Engine
from app.db import get_db, make_engine
from app.domain import RULE_VERSION, SimulationConfig
from app.main import app
from app.models import Assessment, Base, Checkpoint, Job, Outbox, Run, Transaction, User, Wallet


@pytest.fixture
def database(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for PostgreSQL integration")
    assert url.endswith("/aml_test"), "Refusing to reset a non-test database"
    engine = make_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS evaluation"))
        Base.metadata.drop_all(connection)
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    factory = sessionmaker(engine, expire_on_commit=False)

    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(worker, "Session", factory)
    monkeypatch.setattr(worker, "evaluator_session", lambda: factory)
    with factory.begin() as db:
        for name, role in [("test_admin", "Admin"), ("test_viewer", "Viewer")]:
            db.add(
                User(
                    username=name,
                    role=role,
                    password_hash=PasswordHash.recommended().hash("local-test-only-password"),
                )
            )
    yield factory, engine
    app.dependency_overrides.clear()
    engine.dispose()


def login(client, name="test_admin"):
    token = client.get("/api/session").json()["csrf_token"]
    response = client.post(
        "/api/login",
        json={"username": name, "password": "local-test-only-password"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"], "Idempotency-Key": str(uuid4())}


def test_rbac_csrf_idempotency_and_validation(database):
    with TestClient(app) as client:
        assert client.get("/api/simulations").status_code == 401
        headers = login(client)
        config = SimulationConfig(wallet_count=80, transaction_count=150).model_dump()
        assert client.post("/api/simulations", json=config).status_code == 403
        a = client.post("/api/simulations", json=config, headers=headers)
        b = client.post("/api/simulations", json=config, headers=headers)
        assert a.status_code == 202 and a.json() == b.json()
        assert (
            client.post("/api/simulations", json={**config, "seed": 43}, headers=headers).status_code == 409
        )
        headers["Idempotency-Key"] = str(uuid4())
        assert (
            client.post(
                "/api/simulations", json={**config, "patterns": ["not-a-rule"]}, headers=headers
            ).status_code
            == 422
        )
        viewer = login(client, "test_viewer")
        assert client.post("/api/simulations", json=config, headers=viewer).status_code == 403
        assert client.get("/api/simulations").status_code == 200


def test_T17_T21_worker_crash_checkpoint_and_replay(database, monkeypatch):
    factory, _ = database
    with TestClient(app) as client:
        headers = login(client)
        created = client.post(
            "/api/simulations",
            json=SimulationConfig(wallet_count=80, transaction_count=150).model_dump(),
            headers=headers,
        ).json()

        class CrashEngine(Engine):
            def process(self, tx):
                if tx.global_index == 110:
                    raise RuntimeError("Injected crash after checkpoint")
                return super().process(tx)

        monkeypatch.setattr(worker, "Engine", CrashEngine)
        worker.handle(created["job_id"])
        with factory() as db:
            assert db.get(Job, created["job_id"]).status == "failed"
            assert db.get(Checkpoint, (created["run_id"], created["analysis_version"])).global_index == 99
            assert db.scalar(select(func.count()).select_from(Transaction)) == 150
        monkeypatch.setattr(worker, "Engine", Engine)
        assert (
            client.post("/api/simulations/" + created["run_id"] + "/retry", headers=headers).status_code
            == 202
        )
        worker.handle(created["job_id"])
        with factory() as db:
            run = db.get(Run, created["run_id"])
            assert run.status == "completed"
            assert db.scalar(select(func.count()).select_from(Transaction)) == 150
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Assessment)
                    .join(
                        Transaction,
                        (Transaction.run_id == Assessment.run_id)
                        & (Transaction.tx_hash == Assessment.tx_hash),
                    )
                    .where(Assessment.as_of_order == Transaction.global_index)
                )
                == 150
            )
            before = {w.address: (int(w.balance_base), w.next_nonce) for w in db.scalars(select(Wallet))}
            assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.processed.is_(False))) == 0
        alerts = client.get("/api/alerts", params={"run_id": created["run_id"]}).json()["items"]
        assert {
            "STRUCTURING",
            "SMURFING",
            "PEELING",
            "MIXING_PATTERN",
            "RAPID_HIGH_RISK",
            "DIRECT_BLACKLIST",
        } <= {a["detector_code"] for a in alerts}
        alert = alerts[0]
        assert client.get("/api/alerts/" + alert["id"], params={"run_id": str(uuid4())}).status_code == 404
        data = {
            "status": "in_review",
            "comment": "Verified independent fixture",
            "revision": alert["revision"],
        }
        assert (
            client.patch(
                "/api/alerts/" + alert["id"] + "/review",
                params={"run_id": created["run_id"]},
                json=data,
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.patch(
                "/api/alerts/" + alert["id"] + "/review",
                params={"run_id": created["run_id"]},
                json=data,
                headers=headers,
            ).status_code
            == 409
        )
        headers["Idempotency-Key"] = str(uuid4())
        replay = client.post(
            "/api/simulations/" + created["run_id"] + "/replay", json={"mode": "as-known"}, headers=headers
        )
        assert replay.status_code == 202, replay.text
        worker.handle(replay.json()["job_id"])
        with factory() as db:
            assert db.get(Run, created["run_id"]).status == "completed"
            assert db.scalar(select(func.count()).select_from(Transaction)) == 150
            assert {
                w.address: (int(w.balance_base), w.next_nonce) for w in db.scalars(select(Wallet))
            } == before
        first = client.get("/api/transactions", params={"run_id": created["run_id"], "limit": 10}).json()
        second = client.get(
            "/api/transactions",
            params={"run_id": created["run_id"], "limit": 10, "cursor": first["next_cursor"]},
        ).json()
        assert {t["tx_hash"] for t in first["items"]}.isdisjoint({t["tx_hash"] for t in second["items"]})
        assert all(isinstance(t["value_base"], str) for t in first["items"])
        historical = client.get("/api/graph", params={"run_id": created["run_id"], "as_of_index": 0}).json()
        assert all(e["global_index"] <= 0 for e in historical["edges"])
        bounded = client.get(
            "/api/graph", params={"run_id": created["run_id"], "max_nodes": 2, "max_edges": 1}
        ).json()
        assert bounded["truncated"] and len(bounded["nodes"]) <= 2 and len(bounded["edges"]) <= 1
        headers["Idempotency-Key"] = str(uuid4())
        retrospective = client.post(
            "/api/simulations/" + created["run_id"] + "/replay",
            json={"mode": "retrospective"},
            headers=headers,
        )
        assert retrospective.status_code == 202
        worker.handle(retrospective.json()["job_id"])
        metrics = client.get("/api/metrics", params={"run_id": created["run_id"]}).json()
        assert metrics["analysis_mode"] == "retrospective" and "online" not in metrics
        assert "sequential_with_retrospective_feed" in metrics


def test_truth_privilege_and_run_fk_isolation(database):
    factory, engine = database
    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE aml_app"))
            connection.execute(text("SELECT * FROM evaluation.campaigns"))
    # The database constraint rejects a wallet reused in a foreign run transaction.
    with factory.begin() as db:
        run1, run2 = str(uuid4()), str(uuid4())
        config = SimulationConfig(wallet_count=80, transaction_count=150).model_dump()
        db.add_all([Run(id=r, seed=42, config=config, active_version="test") for r in [run1, run2]])
        db.flush()
        db.add(
            Wallet(run_id=run1, id="scoped-wallet", address="sim_0000000000000001", balance_base=2**53 + 1)
        )
    with factory() as db:
        assert int(db.get(Wallet, (run1, "scoped-wallet")).balance_base) == 2**53 + 1
        assert db.get(Wallet, (run2, "scoped-wallet")) is None
        with pytest.raises(IntegrityError):
            db.add(Outbox(run_id=run2, tx_hash="not-in-this-run", analysis_version="test", global_index=0))
            db.commit()


def test_pagerank_only_run_freezes_seed_before_worker_starts(database):
    factory, _ = database
    with TestClient(app) as client:
        created = client.post(
            "/api/simulations",
            json=SimulationConfig(
                seed=142, wallet_count=80, transaction_count=150, patterns=["TAINT_PAGERANK"]
            ).model_dump(),
            headers=login(client),
        )
        assert created.status_code == 202, created.text
        ids = created.json()
        assert ids["analysis_version"].startswith(f"b2-{RULE_VERSION}:")
        with factory() as db:
            job = db.get(Job, ids["job_id"])
            assert job.config["rule_version"] == RULE_VERSION
            assert len(job.config["feed"]) == 1
            frozen = job.config["feed"]
        worker.handle(ids["job_id"])
        with factory() as db:
            assert db.get(Job, ids["job_id"]).config["feed"] == frozen
            assert db.get(Run, ids["run_id"]).status == "completed"
        metrics = client.get("/api/metrics", params={"run_id": ids["run_id"]}).json()
        assert metrics["rule_version"] == RULE_VERSION
        assert metrics["campaign_count"] == metrics["campaigns_detected"] == 1
        alerts = client.get("/api/alerts", params={"run_id": ids["run_id"]}).json()["items"]
        assert "TAINT_PAGERANK" in {a["detector_code"] for a in alerts}


@pytest.mark.parametrize("legacy_version", [None, "rules-v1"])
@pytest.mark.parametrize("kind", ["generate", "replay"])
def test_legacy_job_cannot_resume_with_changed_rules(database, legacy_version, kind):
    factory, _ = database
    with TestClient(app) as client:
        headers = login(client)
        ids = client.post(
            "/api/simulations",
            json=SimulationConfig(wallet_count=80, transaction_count=150).model_dump(),
            headers=headers,
        ).json()
        with factory.begin() as db:
            job = db.get(Job, ids["job_id"])
            job.config = {**job.config, "rule_version": legacy_version}
            job.kind = kind
        worker.handle(ids["job_id"])
        with factory() as db:
            assert db.get(Job, ids["job_id"]).status == "failed"
            assert "Rule version" in db.get(Job, ids["job_id"]).error
            assert db.scalar(select(func.count()).select_from(Transaction)) == 0
            assert db.scalar(select(func.count()).select_from(Assessment)) == 0
            assert db.get(Run, ids["run_id"]).genesis == {}
        response = client.post(f"/api/simulations/{ids['run_id']}/retry", headers=headers)
        assert response.status_code == 409
        assert "Rule version" in response.json()["message"]


def test_episode_quiet_time_crosses_clock_bucket():
    from datetime import timedelta
    from app.domain import GENESIS, Tx

    payload = {
        "anchor": "wallet-a",
        "observed": {},
        "assessment": {"detected_at": (GENESIS + timedelta(minutes=29)).isoformat()},
    }
    transaction = Tx("hash", "wallet-a", "wallet-b", 1, 0, 0, GENESIS + timedelta(minutes=31), 0, 0, 0)
    hit = {"code": "STRUCTURING", "anchor": "wallet-a", "observed": {}}
    assert worker.same_episode(payload, hit, transaction)
    later = Tx("hash", "wallet-a", "wallet-b", 1, 0, 0, GENESIS + timedelta(minutes=60), 0, 0, 0)
    assert not worker.same_episode(payload, hit, later)
    mixing = {**payload, "observed": {"region": ["a", "b"]}}
    assert worker.same_episode(
        mixing, {"code": "MIXING_PATTERN", "observed": {"region": ["a", "b", "c"]}}, transaction
    )
