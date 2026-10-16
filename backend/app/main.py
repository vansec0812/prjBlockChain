import asyncio
import base64
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from uuid import uuid4
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pwdlib import PasswordHash
from sqlalchemy import and_, func, or_, select, text
from starlette.middleware.sessions import SessionMiddleware
from app.aml import Engine
from app.db import Session, get_db
from app.domain import RULE_VERSION, SimulationConfig, Tx, canonical_hash, freeze_feed, synthetic_feed
from app.models import (
    Alert,
    AlertSnapshot,
    AlertTransaction,
    Assessment,
    Audit,
    Event,
    Feed,
    Job,
    Metric,
    Outbox,
    Review,
    Run,
    Transaction,
    User,
    Wallet,
    now,
)

app = FastAPI(
    title="AML Blockchain Simulator", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json"
)
session_secret = os.environ["SESSION_SECRET"]
if len(session_secret) < 32:
    raise RuntimeError("SESSION_SECRET must have at least 32 characters")
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="aml_session",
    same_site="lax",
    max_age=28800,
    https_only=os.getenv("COOKIE_SECURE", "false") == "true",
)
origins = set(os.getenv("ALLOWED_ORIGINS", "http://localhost:8080,http://localhost:8000").split(","))
attempts = defaultdict(deque)


@app.middleware("http")
async def boundary(request: Request, call_next):
    request_id = str(uuid4())
    if request.method in {"POST", "PATCH", "PUT"} and len(await request.body()) > 131072:
        return JSONResponse(
            {
                "code": "body_too_large",
                "message": "Maximum request size is 128 KiB",
                "request_id": request_id,
            },
            status_code=413,
        )
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in origins:
        return JSONResponse(
            {"code": "origin_denied", "message": "Origin is not allowed", "request_id": request_id},
            status_code=403,
        )
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(HTTPException)
async def public_error(request: Request, exc: HTTPException):
    return JSONResponse(
        {
            "code": f"http_{exc.status_code}",
            "message": exc.detail,
            "request_id": getattr(request.state, "request_id", None),
        },
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(
        {
            "code": "validation_error",
            "message": errors[0]["field"] + ": " + errors[0]["message"],
            "details": errors,
            "request_id": getattr(request.state, "request_id", None),
        },
        status_code=422,
    )


@app.exception_handler(Exception)
async def internal_error(request: Request, exc: Exception):
    logging.getLogger("aml.api").error(
        "request_failed id=%s type=%s", getattr(request.state, "request_id", None), type(exc).__name__
    )
    return JSONResponse(
        {
            "code": "internal_error",
            "message": "Lỗi máy chủ. Tra cứu log bằng request_id.",
            "request_id": getattr(request.state, "request_id", None),
        },
        status_code=500,
    )


def current_user(request: Request, db=Depends(get_db)):
    if time.time() - request.session.get("issued_at", 0) > 28800:
        request.session.clear()
        raise HTTPException(401, "Phiên đăng nhập đã hết hạn.")
    user = db.get(User, request.session.get("username", ""))
    if user is None:
        raise HTTPException(401, "Đăng nhập để tiếp tục.")
    return user


def csrf(request: Request):
    token = request.headers.get("X-CSRF-Token", "")
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(token, expected):
        raise HTTPException(403, "Invalid CSRF token")


def admin(user=Depends(current_user), _=Depends(csrf)):
    if user.role != "Admin":
        raise HTTPException(403, "Admin role required")
    return user


def analyst(user=Depends(current_user), _=Depends(csrf)):
    if user.role not in {"Admin", "Analyst"}:
        raise HTTPException(403, "Analyst role required")
    return user


def run_or_404(db, run_id):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "Không tìm thấy lần mô phỏng.")
    return run


def run_dto(run):
    return {
        "id": run.id,
        "seed": run.seed,
        "config": run.config,
        "status": run.status,
        "transaction_count": run.tx_count,
        "analyzed_count": run.analyzed_count,
        "analysis_version": run.active_version,
        "created_at": run.created_at.isoformat(),
        "error": run.error,
        "network_id": run.network_id,
        "asset_id": "SIM",
        "asset_decimals": 6,
    }


def alert_dto(alert):
    return {
        "id": alert.id,
        "run_id": alert.run_id,
        "detector_code": alert.detector_code,
        "score": alert.score,
        "severity": alert.severity,
        "status": alert.status,
        "revision": alert.revision,
        "detected_at": alert.detected_at.isoformat(),
        "detected_order": alert.detected_order,
        "analysis_version": alert.analysis_version,
        "trigger_tx_hash": alert.trigger_tx_hash,
        **alert.payload,
    }


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Replay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="as-known", pattern=r"^(as-known|retrospective)$")


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern=r"^(open|in_review|closed)$")
    comment: str = Field(min_length=1, max_length=2000)
    revision: int = Field(ge=0)


class FeedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network_id: str = Field(default="sim-account-1", pattern=r"^sim-account-1$")
    address: str = Field(pattern=r"^sim_[0-9a-f]{16}$")
    category: str = Field(pattern=r"^(blacklist|high_risk)$")
    severity: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    source_name: str = Field(min_length=1, max_length=64)
    source_ref: str = Field(min_length=1, max_length=512)
    known_at: datetime
    valid_from: datetime
    valid_to: datetime | None = None
    list_version: str = Field(min_length=1, max_length=64)
    enabled: bool = True

    @model_validator(mode="after")
    def check_time(self):
        if any(t and t.tzinfo is None for t in [self.known_at, self.valid_from, self.valid_to]):
            raise ValueError("Timestamps require timezone")
        if self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must exceed valid_from")
        return self


@app.get("/api/health")
def health(db=Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "baseline": "B2", "schema_version": 1}


@app.get("/api/session")
def session(request: Request, db=Depends(get_db)):
    if request.session.get("username") and time.time() - request.session.get("issued_at", 0) > 28800:
        request.session.clear()
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    user = db.get(User, request.session.get("username", ""))
    return {
        "user": {"username": user.username, "role": user.role} if user else None,
        "csrf_token": request.session["csrf"],
    }


@app.post("/api/login", dependencies=[Depends(csrf)])
def login(body: Login, request: Request, db=Depends(get_db)):
    key = request.client.host
    recent = attempts[key]
    cutoff = time.monotonic() - 60
    while recent and recent[0] < cutoff:
        recent.popleft()
    if len(recent) >= 20:
        raise HTTPException(429, "Quá nhiều lần đăng nhập. Thử lại sau một phút.")
    recent.append(time.monotonic())
    user = db.get(User, body.username)
    hasher = PasswordHash.recommended()
    if user is None or not hasher.verify(body.password, user.password_hash):
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng.")
    request.session.clear()
    request.session.update(username=user.username, csrf=secrets.token_urlsafe(32), issued_at=time.time())
    return {"user": {"username": user.username, "role": user.role}, "csrf_token": request.session["csrf"]}


@app.post("/api/logout", dependencies=[Depends(csrf)])
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


def idempotency(db, key, digest):
    if not key or len(key) > 192:
        raise HTTPException(422, "Idempotency-Key (max 100 characters) required")
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
    existing = db.scalar(select(Job).where(Job.idempotency_key == key))
    if existing and existing.request_hash != digest:
        raise HTTPException(409, "Idempotency key already has a different payload")
    return existing


@app.get("/api/simulations")
def runs(_=Depends(current_user), db=Depends(get_db)):
    return [run_dto(r) for r in db.scalars(select(Run).order_by(Run.created_at.desc()).limit(100))]


@app.post("/api/simulations", status_code=202)
def create_simulation(
    body: SimulationConfig,
    user=Depends(admin),
    db=Depends(get_db),
    idempotency_key: str = Header(default="", max_length=100),
):
    try:
        body.checked_patterns()
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    config = body.model_dump()
    digest = canonical_hash(config)
    existing = idempotency(db, user.username + ":" + idempotency_key if idempotency_key else "", digest)
    if existing:
        return {
            "run_id": existing.run_id,
            "job_id": existing.id,
            "analysis_version": existing.analysis_version,
        }
    if db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))) >= 5:
        raise HTTPException(429, "Tối đa 5 công việc trong hàng đợi local.")
    run_id = str(uuid4())
    snapshot = freeze_feed(
        synthetic_feed(body.seed, bool({"RAPID_HIGH_RISK", "TAINT_PAGERANK"} & set(body.patterns)))
        + [f.payload for f in db.scalars(select(Feed))]
    )
    version = (
        f"b2-{RULE_VERSION}:" + canonical_hash([config, snapshot, "as-known", "baseline-A0"])[:12] + ":a1"
    )
    db.add(Run(id=run_id, seed=body.seed, config=config, active_version=version, feed_snapshot=snapshot))
    db.flush()
    job = Job(
        run_id=run_id,
        kind="generate",
        analysis_version=version,
        idempotency_key=user.username + ":" + idempotency_key,
        request_hash=digest,
        config={"feed": snapshot, "mode": "as-known", "rule_version": RULE_VERSION},
    )
    db.add(job)
    db.add(Audit(username=user.username, action="simulation.create", target=run_id, payload=config))
    db.commit()
    return {"run_id": run_id, "job_id": job.id, "analysis_version": version}


@app.get("/api/simulations/{run_id}")
def run_detail(run_id: str, _=Depends(current_user), db=Depends(get_db)):
    result = run_dto(run_or_404(db, run_id))
    result["analyses"] = [
        {
            "version": j.analysis_version,
            "status": j.status,
            "kind": j.kind,
            "mode": j.config.get("mode", "as-known"),
            "job_id": j.id,
            "error": j.error,
        }
        for j in db.scalars(select(Job).where(Job.run_id == run_id).order_by(Job.created_at.desc()))
    ]
    return result


@app.post("/api/simulations/{run_id}/replay", status_code=202)
def replay(
    run_id: str,
    body: Replay,
    user=Depends(admin),
    db=Depends(get_db),
    idempotency_key: str = Header(default="", max_length=100),
):
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise HTTPException(404, "Run not found")
    digest = canonical_hash([run_id, body.mode])
    existing = idempotency(db, user.username + ":" + idempotency_key if idempotency_key else "", digest)
    if existing:
        return {"run_id": run_id, "job_id": existing.id, "analysis_version": existing.analysis_version}
    if run.status != "completed":
        raise HTTPException(409, "Replay yêu cầu ledger đã hoàn thành.")
    count = db.scalar(select(func.count()).select_from(Job).where(Job.run_id == run_id))
    feed = freeze_feed([f.payload for f in db.scalars(select(Feed))])
    version = f"b2-{RULE_VERSION}:" + canonical_hash([feed, body.mode])[:12] + f":a{count + 1}"
    job = Job(
        run_id=run_id,
        kind="replay",
        analysis_version=version,
        idempotency_key=user.username + ":" + idempotency_key,
        request_hash=digest,
        config={
            "feed": feed,
            "mode": body.mode,
            "as_of_time": now().isoformat(),
            "rule_version": RULE_VERSION,
        },
    )
    db.add(job)
    run.active_version, run.status, run.analyzed_count = version, "queued", 0
    for tx in db.scalars(select(Transaction).where(Transaction.run_id == run_id)):
        db.add(
            Outbox(run_id=run_id, tx_hash=tx.tx_hash, analysis_version=version, global_index=tx.global_index)
        )
    db.add(
        Audit(
            username=user.username,
            action="analysis.replay",
            target=run_id,
            payload={"version": version, "mode": body.mode},
        )
    )
    db.commit()
    return {"run_id": run_id, "job_id": job.id, "analysis_version": version}


@app.post("/api/simulations/{run_id}/retry", status_code=202)
def retry(run_id: str, user=Depends(admin), db=Depends(get_db)):
    run = run_or_404(db, run_id)
    job = db.scalar(
        select(Job)
        .where(Job.run_id == run_id, Job.analysis_version == run.active_version)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if run.status != "failed" or job is None:
        raise HTTPException(409, "Không có công việc thất bại để thử lại.")
    if job.config.get("rule_version") != RULE_VERSION:
        raise HTTPException(409, "Rule version đã thay đổi; tạo run mới hoặc replay ledger đã hoàn thành.")
    job.status, job.error, run.status, run.error = "queued", None, "queued", None
    db.add(Audit(username=user.username, action="job.retry", target=job.id, payload={}))
    db.commit()
    return {"job_id": job.id}


def cutoff_index(run, as_of_index):
    return min(as_of_index, run.tx_count - 1) if as_of_index is not None else run.tx_count - 1


def assessment_query(run_id, version, cutoff):
    latest = (
        select(Assessment.tx_hash, func.max(Assessment.as_of_order).label("latest"))
        .where(
            Assessment.run_id == run_id,
            Assessment.analysis_version == version,
            Assessment.as_of_order <= cutoff,
        )
        .group_by(Assessment.tx_hash)
        .subquery()
    )
    return latest


@app.get("/api/transactions")
def transactions(
    run_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    wallet: str | None = Query(None, max_length=64),
    min_score: int = Query(0, ge=0, le=100),
    as_of_index: int | None = Query(None, ge=0),
    analysis_version: str | None = None,
    _=Depends(current_user),
    db=Depends(get_db),
):
    run = run_or_404(db, run_id)
    cutoff = cutoff_index(run, as_of_index)
    version = analysis_version or run.active_version
    signature = canonical_hash([run_id, version, wallet, min_score, as_of_index])
    start = -1
    if cursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor))
            if data["signature"] != signature or type(data["after"]) is not int or data["after"] < -1:
                raise ValueError()
            start = data["after"]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "Cursor does not match filters/run/version")
    latest = assessment_query(run_id, version, cutoff)
    query = (
        select(Transaction, Assessment)
        .outerjoin(latest, latest.c.tx_hash == Transaction.tx_hash)
        .outerjoin(
            Assessment,
            and_(
                Assessment.run_id == Transaction.run_id,
                Assessment.tx_hash == Transaction.tx_hash,
                Assessment.analysis_version == version,
                Assessment.as_of_order == latest.c.latest,
            ),
        )
        .where(
            Transaction.run_id == run_id, Transaction.global_index > start, Transaction.global_index <= cutoff
        )
    )
    if wallet:
        query = query.where(or_(Transaction.from_address == wallet, Transaction.to_address == wallet))
    if min_score:
        query = query.where(Assessment.score >= min_score)
    rows = db.execute(query.order_by(Transaction.global_index).limit(limit + 1)).all()
    items = [{**t.payload, "assessment": a.payload if a else None} for t, a in rows[:limit]]
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps({"signature": signature, "after": rows[limit - 1][0].global_index}).encode()
        ).decode()
        if len(rows) > limit
        else None
    )
    return {"items": items, "next_cursor": next_cursor, "as_of_index": cutoff, "analysis_version": version}


@app.get("/api/alerts")
def alerts(
    run_id: str,
    status: str | None = Query(None, pattern=r"^(open|in_review|closed)$"),
    detector: str | None = None,
    analysis_version: str | None = None,
    as_of_index: int | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _=Depends(current_user),
    db=Depends(get_db),
):
    run = run_or_404(db, run_id)
    query = select(Alert).where(
        Alert.run_id == run_id,
        Alert.analysis_version == (analysis_version or run.active_version),
        Alert.detected_order <= cutoff_index(run, as_of_index),
    )
    if status:
        query = query.where(Alert.status == status)
    if detector:
        query = query.where(Alert.detector_code == detector)
    items = list(
        db.scalars(query.order_by(Alert.detected_at.desc(), Alert.id).offset(offset).limit(limit + 1))
    )
    visible = items[:limit]
    snapshots = {}
    if as_of_index is not None and visible:
        rows = db.scalars(
            select(AlertSnapshot)
            .where(
                AlertSnapshot.run_id == run_id,
                AlertSnapshot.alert_id.in_([a.id for a in visible]),
                AlertSnapshot.as_of_order <= as_of_index,
            )
            .order_by(AlertSnapshot.as_of_order.desc())
        )
        for snapshot in rows:
            snapshots.setdefault(snapshot.alert_id, snapshot.payload)
    return {
        "items": [{**alert_dto(a), **snapshots.get(a.id, {})} for a in visible],
        "has_more": len(items) > limit,
    }


@app.get("/api/alerts/{alert_id}")
def alert_detail(
    alert_id: str,
    run_id: str,
    as_of_index: int | None = Query(None, ge=0),
    _=Depends(current_user),
    db=Depends(get_db),
):
    alert = db.get(Alert, (run_id, alert_id))
    if alert is None:
        raise HTTPException(404, "Không tìm thấy cảnh báo trong run.")
    result = alert_dto(alert)
    if as_of_index is not None:
        snapshot = db.scalar(
            select(AlertSnapshot)
            .where(
                AlertSnapshot.run_id == run_id,
                AlertSnapshot.alert_id == alert_id,
                AlertSnapshot.as_of_order <= as_of_index,
            )
            .order_by(AlertSnapshot.as_of_order.desc())
            .limit(1)
        )
        if snapshot is None:
            raise HTTPException(404, "Alert not available at this cutoff")
        result.update(snapshot.payload)
    result["transactions"] = [
        t.payload
        for t in db.scalars(
            select(Transaction)
            .join(
                AlertTransaction,
                and_(
                    AlertTransaction.run_id == Transaction.run_id,
                    AlertTransaction.tx_hash == Transaction.tx_hash,
                ),
            )
            .where(
                AlertTransaction.run_id == run_id,
                AlertTransaction.alert_id == alert_id,
                Transaction.tx_hash.in_(result["evidence"]),
            )
            .order_by(Transaction.global_index)
        )
    ]
    result["reviews"] = [
        {
            "username": r.username,
            "old_status": r.old_status,
            "status": r.status,
            "comment": r.comment,
            "created_at": r.created_at.isoformat(),
        }
        for r in db.scalars(
            select(Review)
            .where(Review.run_id == run_id, Review.alert_id == alert_id)
            .order_by(Review.created_at)
        )
    ]
    return result


@app.patch("/api/alerts/{alert_id}/review")
def review(alert_id: str, run_id: str, body: ReviewInput, user=Depends(analyst), db=Depends(get_db)):
    alert = db.scalar(select(Alert).where(Alert.run_id == run_id, Alert.id == alert_id).with_for_update())
    if alert is None:
        raise HTTPException(404, "Không tìm thấy cảnh báo trong run.")
    if body.revision != alert.revision:
        raise HTTPException(409, "Cảnh báo đã thay đổi. Tải lại trước khi đánh giá.")
    db.add(
        Review(
            run_id=run_id,
            alert_id=alert_id,
            username=user.username,
            old_status=alert.status,
            status=body.status,
            comment=body.comment.strip(),
        )
    )
    db.add(
        Audit(
            username=user.username,
            action="alert.review",
            target=alert_id,
            payload={"run_id": run_id, "old_status": alert.status, "status": body.status},
        )
    )
    alert.status, alert.revision = body.status, alert.revision + 1
    db.commit()
    return alert_dto(alert)


@app.get("/api/metrics")
def metrics(run_id: str, analysis_version: str | None = None, _=Depends(current_user), db=Depends(get_db)):
    run = run_or_404(db, run_id)
    metric = db.get(Metric, (run_id, analysis_version or run.active_version))
    return metric.payload if metric else {"status": "pending", "scope": "Evaluation not completed"}


@app.get("/api/wallets/{wallet_id}")
def wallet_detail(
    wallet_id: str,
    run_id: str,
    as_of_index: int | None = Query(None, ge=0),
    _=Depends(current_user),
    db=Depends(get_db),
):
    run = run_or_404(db, run_id)
    wallet = db.scalar(
        select(Wallet).where(
            Wallet.run_id == run_id, or_(Wallet.id == wallet_id, Wallet.address == wallet_id)
        )
    )
    if wallet is None:
        raise HTTPException(404, "Wallet not found in run")
    cutoff = cutoff_index(run, as_of_index)
    incoming = int(
        db.scalar(
            select(func.coalesce(func.sum(Transaction.value_base), 0)).where(
                Transaction.run_id == run_id,
                Transaction.to_address == wallet.address,
                Transaction.global_index <= cutoff,
            )
        )
    )
    outgoing = int(
        db.scalar(
            select(func.coalesce(func.sum(Transaction.value_base + Transaction.fee_base), 0)).where(
                Transaction.run_id == run_id,
                Transaction.from_address == wallet.address,
                Transaction.global_index <= cutoff,
            )
        )
    )
    balance = int(run.genesis.get(wallet.address, "0")) + incoming - outgoing
    if wallet.is_system:
        balance = int(
            db.scalar(
                select(func.coalesce(func.sum(Transaction.fee_base), 0)).where(
                    Transaction.run_id == run_id, Transaction.global_index <= cutoff
                )
            )
        )
    return {
        "id": wallet.id,
        "address": wallet.address,
        "run_id": run_id,
        "balance_base": str(balance),
        "inflow_base": str(incoming),
        "outflow_with_fees_base": str(outgoing),
        "as_of_index": cutoff,
        "asset_id": "SIM",
        "asset_decimals": 6,
    }


@app.get("/api/graph")
def graph(
    run_id: str,
    center: str | None = Query(None, max_length=64),
    hops: int = Query(2, ge=1, le=5),
    as_of_index: int | None = Query(None, ge=0),
    alert_id: str | None = None,
    analysis_version: str | None = None,
    max_nodes: int = Query(300, ge=2, le=1000),
    max_edges: int = Query(600, ge=1, le=3000),
    _=Depends(current_user),
    db=Depends(get_db),
):
    run = run_or_404(db, run_id)
    cutoff = cutoff_index(run, as_of_index)
    version = analysis_version or run.active_version
    base = select(Transaction).where(Transaction.run_id == run_id, Transaction.global_index <= cutoff)
    last = db.scalar(base.order_by(Transaction.global_index.desc()).limit(1))
    if last and not alert_id:
        base = base.where(Transaction.timestamp >= last.timestamp - timedelta(hours=24))
    rows, truncated = [], False
    if alert_id:
        alert = db.get(Alert, (run_id, alert_id))
        if alert is None or alert.detected_order > cutoff:
            raise HTTPException(404, "Alert is not available at this cutoff")
        version = alert.analysis_version
        rows = list(
            db.scalars(
                base.where(Transaction.tx_hash.in_(alert.payload["evidence"]))
                .order_by(Transaction.global_index)
                .limit(max_edges + 1)
            )
        )
    elif center:
        frontier, seen, edges = {center}, {center}, {}
        for _hop in range(hops):
            found = list(
                db.scalars(
                    base.where(
                        or_(Transaction.from_address.in_(frontier), Transaction.to_address.in_(frontier))
                    )
                    .order_by(Transaction.global_index.desc())
                    .limit(max_edges + 1)
                )
            )
            for row in found:
                edges[row.tx_hash] = row
            new = {a for t in found for a in [t.from_address, t.to_address]} - seen
            seen.update(new)
            frontier = new
            if len(edges) > max_edges or len(seen) > max_nodes:
                truncated = True
                break
            if not frontier:
                break
        rows = sorted(edges.values(), key=lambda t: t.global_index)
    else:
        rows = list(db.scalars(base.order_by(Transaction.global_index.desc()).limit(max_edges + 1)))
        rows.reverse()
    if len(rows) > max_edges:
        truncated = True
    rows = rows[:max_edges]
    addresses, included = set(), []
    for row in rows:
        nodes = {row.from_address, row.to_address}
        if len(addresses | nodes) > max_nodes:
            truncated = True
            continue
        addresses.update(nodes)
        included.append(row)
    latest = assessment_query(run_id, version, cutoff)
    scores = {}
    if addresses:
        query = (
            select(Transaction, Assessment)
            .join(latest, latest.c.tx_hash == Transaction.tx_hash)
            .join(
                Assessment,
                and_(
                    Assessment.run_id == Transaction.run_id,
                    Assessment.tx_hash == Transaction.tx_hash,
                    Assessment.analysis_version == version,
                    Assessment.as_of_order == latest.c.latest,
                ),
            )
            .where(
                Transaction.run_id == run_id,
                or_(Transaction.from_address.in_(addresses), Transaction.to_address.in_(addresses)),
            )
        )
        # One bounded batch, no per-node query. The latest as-of assessment wins.
        assessed = db.execute(
            query.order_by(Assessment.as_of_order.desc(), Transaction.global_index.desc()).limit(20000)
        ).all()
        if len(assessed) >= 20000:
            truncated = True
        for tx, assessment in assessed:
            for address in [tx.from_address, tx.to_address]:
                if address in addresses and address not in scores:
                    scores[address] = assessment.payload
    analysis_job = db.scalar(select(Job).where(Job.run_id == run_id, Job.analysis_version == version))
    snapshot = analysis_job.config if analysis_job else {"feed": run.feed_snapshot}
    matcher = Engine(
        {},
        snapshot.get("feed", []),
        mode=snapshot.get("mode", "as-known"),
        as_of_time=datetime.fromisoformat(snapshot["as_of_time"]) if snapshot.get("as_of_time") else None,
    )
    nodes = [
        {
            "id": a,
            "label": a[:8] + "…" + a[-4:],
            "score": scores[a]["score"] if a in scores else None,
            "severity": scores[a]["severity"] if a in scores else "Unknown",
            "blacklisted": bool(
                last
                and any(
                    s["category"] == "blacklist" for s in matcher.sources(a, Tx.from_payload(last.payload))
                )
            ),
            "coverage": scores[a]["coverage"] if a in scores else "pending",
            "assessed_at": scores[a].get("detected_at") if a in scores else None,
            "as_of_order": scores[a].get("as_of_order") if a in scores else None,
            "analysis_version": version,
        }
        for a in sorted(addresses)
    ]
    return {
        "nodes": nodes,
        "edges": [
            {
                "id": t.tx_hash,
                "source": t.from_address,
                "target": t.to_address,
                "value_base": str(int(t.value_base)),
                "fee_base": str(int(t.fee_base)),
                "block_height": t.block_height,
                "tx_index": t.tx_index,
                "timestamp": t.timestamp.isoformat(),
                "global_index": t.global_index,
                "tx_hash": t.tx_hash,
            }
            for t in included
        ],
        "truncated": truncated,
        "coverage": "partial" if truncated or any(n["coverage"] != "complete" for n in nodes) else "complete",
        "as_of_index": cutoff,
        "analysis_version": version,
        "analysis_mode": snapshot.get("mode", "as-known"),
        "asset_decimals": 6,
    }


@app.get("/api/feeds")
def feeds(_=Depends(current_user), db=Depends(get_db)):
    return [f.payload for f in db.scalars(select(Feed).order_by(Feed.known_at.desc()).limit(200))]


@app.post("/api/feeds", status_code=201)
def add_feed(body: FeedInput, user=Depends(admin), db=Depends(get_db)):
    if db.scalar(
        select(Feed).where(
            Feed.network_id == body.network_id,
            Feed.address == body.address,
            Feed.source_name == body.source_name,
            Feed.list_version == body.list_version,
        )
    ):
        raise HTTPException(409, "Feed version already exists")
    payload = body.model_dump(mode="json")
    db.add(
        Feed(
            network_id=body.network_id,
            address=body.address,
            known_at=body.known_at,
            source_name=body.source_name,
            list_version=body.list_version,
            payload=payload,
        )
    )
    db.add(Audit(username=user.username, action="feed.create", target=body.address, payload=payload))
    db.commit()
    return payload


@app.get("/api/events")
async def events(request: Request, run_id: str | None = None, last_event_id: str | None = Header(None)):
    issued_at = request.session.get("issued_at", 0)
    if time.time() - issued_at > 28800:
        raise HTTPException(401, "Session expired")
    with Session() as db:
        if db.get(User, request.session.get("username", "")) is None:
            raise HTTPException(401, "Login required")
        if run_id:
            run_or_404(db, run_id)
        latest = db.scalar(select(func.max(Event.id))) or 0
    try:
        cursor = int(last_event_id) if last_event_id else latest
    except ValueError:
        raise HTTPException(422, "Invalid Last-Event-ID")
    if cursor < 0:
        raise HTTPException(422, "Invalid Last-Event-ID")

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected() and time.time() - issued_at < 28800:
            with Session() as db:
                minimum = db.scalar(select(func.min(Event.id))) or 0
                query = select(Event).where(Event.id > cursor).order_by(Event.id).limit(100)
                if run_id:
                    query = query.where(Event.run_id == run_id)
                rows = list(db.scalars(query))
            if minimum and cursor and cursor < minimum - 1:
                yield "event: resync\ndata: {}\n\n"
                cursor = minimum - 1
            for event in rows:
                cursor = event.id
                yield f"id: {event.id}\nevent: {event.kind}\ndata: {json.dumps({'schema_version': 1, 'run_id': event.run_id, **event.payload})}\n\n"
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
