"""Exact ledger and canonical simulator hashes, independent of transport/database."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pydantic import BaseModel, ConfigDict, Field

UNIT = 1_000_000
THRESHOLD = 1_000 * UNIT
FEE = 21_000
GENESIS = datetime(2026, 9, 8, tzinfo=timezone.utc)
RULE_VERSION = "rules-v1"
PATTERNS = [
    "STRUCTURING",
    "SMURFING",
    "PEELING",
    "MIXING_PATTERN",
    "RAPID_HIGH_RISK",
    "CIRCULAR_FLOW",
    "U_TURN_BOUNCE",
    "TAINT_PAGERANK",
]


def freeze_feed(records: list[dict]) -> list[dict]:
    unique = {(f["network_id"], f["address"], f["source_name"], f["list_version"]): f for f in records}
    return [unique[key] for key in sorted(unique)]


def synthetic_feed(seed: int, enabled: bool = True) -> list[dict]:
    if not enabled:
        return []
    return [
        {
            "network_id": "sim-account-1",
            "address": "sim_" + canonical_hash(["address-v1", seed, 52])[:16],
            "category": "blacklist",
            "severity": 95,
            "confidence": 1.0,
            "source_name": "Synthetic TI",
            "source_ref": "synthetic://ti-v1",
            "known_at": GENESIS.isoformat(),
            "valid_from": GENESIS.isoformat(),
            "valid_to": None,
            "list_version": f"synthetic-v1-{seed}",
            "enabled": True,
        }
    ]


def canonical_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def money(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,77}", value):
        raise ValueError("Expected a nonnegative integer base-unit string (max 78 digits)")
    return int(value)


def address_valid(address: str) -> bool:
    return bool(re.fullmatch(r"sim_[0-9a-f]{16}", address))


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    wallet_count: int = Field(default=500, ge=80, le=10000)
    transaction_count: int = Field(default=5000, ge=150, le=100000)
    simulation_days: int = Field(default=7, ge=1, le=30)
    block_interval_seconds: int = Field(default=12, ge=1, le=60)
    max_transactions_per_block: int = Field(default=100, ge=1, le=100)
    patterns: list[str] = Field(default_factory=lambda: PATTERNS.copy(), max_length=8)

    def checked_patterns(self):
        if len(set(self.patterns)) != len(self.patterns) or any(p not in PATTERNS for p in self.patterns):
            raise ValueError("Unknown or duplicate scenario pattern")
        return self


@dataclass(frozen=True)
class Tx:
    tx_hash: str
    sender: str
    recipient: str
    value: int
    fee: int
    nonce: int
    timestamp: datetime
    height: int
    index: int
    global_index: int
    block_hash: str = ""

    @property
    def order(self):
        return self.height, self.index

    def payload(self) -> dict:
        return {
            "schema_version": 1,
            "network_id": "sim-account-1",
            "asset_id": "SIM",
            "hash_scheme": "sim-sha256-v1",
            "tx_hash": self.tx_hash,
            "block_hash": self.block_hash,
            "block_height": self.height,
            "tx_index": self.index,
            "global_index": self.global_index,
            "from_address": self.sender,
            "to_address": self.recipient,
            "value_base": str(self.value),
            "fee_base": str(self.fee),
            "nonce": self.nonce,
            "timestamp": self.timestamp.isoformat(),
            "gas_limit": 21000,
            "gas_used": 21000,
            "effective_gas_price_base": "1",
            "status": "success",
        }

    @classmethod
    def from_payload(cls, data: dict):
        if (
            data.get("schema_version") != 1
            or data.get("network_id") != "sim-account-1"
            or data.get("asset_id") != "SIM"
            or data.get("hash_scheme") != "sim-sha256-v1"
            or data.get("status") != "success"
        ):
            raise ValueError("Unsupported transaction schema, network, asset or status")
        if (
            type(data["gas_used"]) is not int
            or type(data["gas_limit"]) is not int
            or not 0 <= data["gas_used"] <= data["gas_limit"]
            or money(data["fee_base"]) != data["gas_used"] * money(data["effective_gas_price_base"])
        ):
            raise ValueError("Invalid gas/fee contract")
        return cls(
            data["tx_hash"],
            data["from_address"],
            data["to_address"],
            money(data["value_base"]),
            money(data["fee_base"]),
            data["nonce"],
            datetime.fromisoformat(data["timestamp"]),
            data["block_height"],
            data["tx_index"],
            data["global_index"],
            data.get("block_hash", ""),
        )


@dataclass
class Ledger:
    balances: dict[str, int]
    nonces: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        self.balances = self.balances.copy()
        self.balances.setdefault("fee_sink", 0)
        if any(type(v) is not int or v < 0 or v >= 10**78 for v in self.balances.values()):
            raise ValueError("Invalid genesis balance")
        self.supply = sum(self.balances.values())

    def apply(self, sender: str, recipient: str, value: int, nonce: int, fee: int = FEE):
        if not address_valid(sender) or not address_valid(recipient):
            raise ValueError("Invalid address")
        if type(value) is not int or value <= 0 or value >= 10**78:
            raise ValueError("Invalid value")
        if type(nonce) is not int or nonce != self.nonces.get(sender, 0):
            raise ValueError("Invalid nonce")
        if type(fee) is not int or fee != FEE:
            raise ValueError("Invalid fee")
        if self.balances.get(sender, 0) < value + fee:
            raise ValueError("Insufficient balance")
        self.balances[sender] -= value + fee
        self.balances[recipient] = self.balances.get(recipient, 0) + value
        self.balances["fee_sink"] += fee
        self.nonces[sender] = nonce + 1
        if sum(self.balances.values()) != self.supply or min(self.balances.values()) < 0:
            raise AssertionError("Ledger conservation violated")

    def checksum(self):
        return canonical_hash(
            [[a, str(self.balances[a]), self.nonces.get(a, 0)] for a in sorted(self.balances)]
        )


def merkle(hashes: list[str]) -> str:
    if not hashes:
        return hashlib.sha256(b"").hexdigest()
    nodes = [bytes.fromhex(h) for h in hashes]
    while len(nodes) > 1:
        if len(nodes) % 2:
            nodes.append(nodes[-1])
        nodes = [hashlib.sha256(nodes[i] + nodes[i + 1]).digest() for i in range(0, len(nodes), 2)]
    return nodes[0].hex()


def score(components: dict[str, float], codes: list[str]) -> dict:
    if any(not 0 <= v <= 1 for v in components.values()):
        raise ValueError("Score component out of bounds")
    weights = {"E": ".35", "P": ".30", "V": ".15", "Q": ".10", "A": ".10"}
    blend = sum(Decimal(str(components[k])) * Decimal(w) for k, w in weights.items()) * 100
    floors = {
        code: (95 if code == "DIRECT_BLACKLIST" else 80 if code == "RAPID_HIGH_RISK" else 70)
        for code in codes
        if code in PATTERNS or code == "DIRECT_BLACKLIST"
    }
    final = int(
        min(Decimal(100), max(blend, Decimal(max(floors.values(), default=0)))).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )
    severity = (
        "Low"
        if final < 30
        else "Medium"
        if final < 50
        else "Elevated"
        if final < 70
        else "High"
        if final < 90
        else "Critical"
    )
    return {
        "components": components,
        "blend": float(blend),
        "floors": floors,
        "score": final,
        "severity": severity,
        "model_status": "not_configured",
        "model_version": "baseline-A0",
        "rule_version": RULE_VERSION,
    }
