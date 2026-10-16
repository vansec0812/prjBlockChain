"""Online baseline B2. This module deliberately has no evaluation/label imports."""

import math
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import networkx as nx
from app.domain import THRESHOLD, UNIT, Ledger, Tx, canonical_hash, score


@dataclass(frozen=True)
class Part:
    amount: int
    origin: str
    source: dict | None = None
    path: tuple[Tx, ...] = ()
    started: datetime | None = None
    truncated: bool = False
    received_amount: int = 0
    forward_ratios: tuple[tuple[int, int], ...] = ()


def apportion(parts: list[Part], amount: int) -> tuple[list[Part], list[Part]]:
    """Hamilton apportionment; row sum and withdrawal sum remain exact integers."""
    total = sum(p.amount for p in parts)
    if amount < 0 or amount > total:
        raise ValueError("Allocation exceeds available balance")
    if not total:
        return [], parts
    shares = [p.amount * amount // total for p in parts]
    remainders = [p.amount * amount % total for p in parts]
    for i in sorted(range(len(parts)), key=lambda i: (-remainders[i], i))[: amount - sum(shares)]:
        shares[i] += 1
    sent = [replace(p, amount=v) for p, v in zip(parts, shares) if v]
    kept = [replace(p, amount=p.amount - v) for p, v in zip(parts, shares) if p.amount > v]
    assert sum(p.amount for p in sent) == amount
    assert sum(p.amount for p in kept) == total - amount
    return sent, kept


class Engine:
    def __init__(
        self,
        genesis: dict[str, int],
        feed: list[dict],
        mode="as-known",
        as_of_time=None,
        edge_budget=20000,
        path_budget=100,
        run_id="unit-run",
    ):
        self.ledger = Ledger(genesis)
        self.run_id = run_id
        self.parts = {a: [Part(v, a)] if v else [] for a, v in self.ledger.balances.items()}
        self.feed = feed
        self.feed_index = defaultdict(list)
        for source in feed:
            self.feed_index[(source["network_id"], source["address"])].append(source)
        self.mode = mode
        self.as_of_time = as_of_time or datetime.now(timezone.utc)
        self.edge_budget, self.path_budget = edge_budget, path_budget
        self.graph = nx.MultiDiGraph()
        self.recent = deque()
        self.out = defaultdict(deque)
        self.incoming = defaultdict(deque)
        self.flows = defaultdict(deque)
        self.last_in = {}
        self.peel = {}
        self.candidates = set()
        self.last_order = (-1, -1)
        self.last_timestamp = None
        self.balance_changes = defaultdict(deque)

    def node(self, address: str):
        return self.run_id, address

    def sources(self, address: str, tx: Tx):
        result = []
        for source in self.feed_index[("sim-account-1", address)]:
            cutoff = tx.timestamp if self.mode == "as-known" else self.as_of_time
            if (
                source.get("enabled", True)
                and source["network_id"] == "sim-account-1"
                and source["address"] == address
                and datetime.fromisoformat(source["known_at"]) <= cutoff
                and datetime.fromisoformat(source["valid_from"]) <= tx.timestamp
                and (
                    source.get("valid_to") is None
                    or tx.timestamp < datetime.fromisoformat(source["valid_to"])
                )
            ):
                result.append(source)
        return sorted(
            result, key=lambda s: (-s["severity"] * s["confidence"], s["list_version"], s["source_name"])
        )

    def window(self, wallet: str, tx: Tx, seconds: int, incoming=False):
        entries = self.incoming[wallet] if incoming else self.out[wallet]
        lower = tx.timestamp - timedelta(seconds=seconds)
        return [(t, b) for t, b in entries if lower <= t.timestamp <= tx.timestamp and t.order <= tx.order]

    def compact(self, address: str):
        parts = self.parts.get(address, [])
        if len(parts) <= 128:
            return
        grouped = {}
        for p in parts:
            key = (p.source["address"], p.source["list_version"]) if p.source else ("normal", "")
            if key not in grouped:
                grouped[key] = replace(p, origin="", path=(), truncated=True)
            else:
                grouped[key] = replace(grouped[key], amount=grouped[key].amount + p.amount)
        self.parts[address] = list(grouped.values())

    def process(self, tx: Tx) -> dict:
        if tx.order <= self.last_order:
            raise ValueError("Transactions must arrive in strict ledger order")
        if self.last_timestamp is not None and tx.timestamp < self.last_timestamp:
            raise ValueError("Ledger event timestamps must not decrease")
        self.last_order = tx.order
        self.last_timestamp = tx.timestamp
        before = self.ledger.balances.get(tx.sender, 0)
        balances_before = {a: self.ledger.balances.get(a, 0) for a in {tx.sender, tx.recipient, "fee_sink"}}
        sender_sources, recipient_sources = self.sources(tx.sender, tx), self.sources(tx.recipient, tx)
        # New TI labels only previously unassigned portions, never multiply exposure.
        if sender_sources:
            self.parts[tx.sender] = [
                replace(
                    p,
                    source=sender_sources[0],
                    started=tx.timestamp,
                    path=(),
                    truncated=False,
                    received_amount=0,
                    forward_ratios=(),
                )
                if p.source is None
                else p
                for p in self.parts.get(tx.sender, [])
            ]
        sent, kept = apportion(self.parts.get(tx.sender, []), tx.value)
        fees, kept = apportion(kept, tx.fee)
        self.parts[tx.sender] = kept
        self.parts.setdefault("fee_sink", []).extend(fees)
        moved = []
        for p in sent:
            path = p.path + (tx,)
            ratios = (
                p.forward_ratios + ((p.amount, p.received_amount),) if p.received_amount else p.forward_ratios
            )
            moved.append(
                replace(
                    p,
                    path=path[-5:],
                    truncated=p.truncated or len(path) > 5,
                    received_amount=p.amount,
                    forward_ratios=ratios[-4:],
                )
            )
        if tx.sender == tx.recipient:
            self.parts[tx.sender].extend(sent)
            moved = []
        else:
            self.parts.setdefault(tx.recipient, []).extend(moved)
        self.ledger.apply(tx.sender, tx.recipient, tx.value, tx.nonce, tx.fee)
        for a, old_balance in balances_before.items():
            changes = self.balance_changes[a]
            changes.append((tx, self.ledger.balances[a] - old_balance))
            while changes and changes[0][0].timestamp < tx.timestamp - timedelta(hours=24):
                changes.popleft()
        for a in {tx.sender, tx.recipient, "fee_sink"}:
            self.compact(a)
            assert sum(p.amount for p in self.parts[a]) == self.ledger.balances[a]
        cutoff = tx.timestamp - timedelta(hours=24)
        while self.recent and self.recent[0].timestamp < cutoff:
            old = self.recent.popleft()
            if self.graph.has_edge(self.node(old.sender), self.node(old.recipient), old.tx_hash):
                self.graph.remove_edge(self.node(old.sender), self.node(old.recipient), old.tx_hash)
        self.recent.append(tx)
        self.graph.add_edge(
            self.node(tx.sender),
            self.node(tx.recipient),
            key=tx.tx_hash,
            tx=tx,
            value_base=tx.value,
            asset_id="SIM",
            timestamp=tx.timestamp,
            order=tx.order,
            fee_base=tx.fee,
        )
        for a in {tx.sender, tx.recipient}:
            for collection in (self.out[a], self.incoming[a], self.flows[a]):
                while collection and collection[0][0].timestamp < cutoff:
                    collection.popleft()
        self.out[tx.sender].append((tx, before))
        self.incoming[tx.recipient].append((tx, before))
        self.flows[tx.recipient].append((tx, moved))
        hits = []
        partial_reasons = set()
        if any(p.truncated for p in moved):
            partial_reasons.add("provenance_path_compacted")
        if tx.sender != tx.recipient:
            hits.extend(self.structuring(tx))
            hits.extend(self.smurfing(tx, partial_reasons))
            hits.extend(self.peeling(tx))
            hits.extend(self.mixing(tx, partial_reasons))
            hits.extend(self.circular_flow(tx, moved))
        direct = [(s, direction) for s in sender_sources for direction in ["sender"]] + [
            (s, "recipient") for s in recipient_sources
        ]
        blacklist = [(s, d) for s, d in direct if s["category"] == "blacklist"]
        if blacklist:
            hits.append(
                self.hit(
                    "DIRECT_BLACKLIST",
                    tx.sender if blacklist[0][1] == "sender" else tx.recipient,
                    [tx],
                    {"matches": [{**s, "direction": d} for s, d in blacklist]},
                    {"match": "exact network/address + valid feed"},
                )
            )
        exposure = 0.0
        rapid = []
        for p in moved:
            if not p.source:
                continue
            if p.truncated or not p.path or not p.started:
                partial_reasons.add("exposure_outside_trace_scope")
                continue
            h = len(p.path)
            delta = (tx.timestamp - p.started).total_seconds()
            if delta > 86400:
                partial_reasons.add("exposure_outside_trace_scope")
                continue
            exposure += (
                p.amount
                / tx.value
                * p.source["confidence"]
                * p.source["severity"]
                / 100
                * 0.7 ** (h - 1)
                * math.exp(-delta / 86400)
            )
            if (
                3 <= h <= 5
                and p.source["severity"] >= 80
                and p.source["confidence"] >= 0.8
                and all(
                    0 <= (b.timestamp - a.timestamp).total_seconds() <= 120
                    for a, b in zip(p.path, p.path[1:])
                )
                and all(forwarded * 100 >= received * 80 for forwarded, received in p.forward_ratios)
            ):
                rapid.append(p)
        if rapid:
            grouped_paths = {}
            for p in rapid:
                key = (p.source["address"], p.source["list_version"], tuple(t.tx_hash for t in p.path))
                grouped_paths[key] = (
                    replace(p, amount=grouped_paths[key].amount + p.amount) if key in grouped_paths else p
                )
            paths = sorted(
                grouped_paths.values(),
                key=lambda p: (-p.amount, p.source["address"], tuple(t.tx_hash for t in p.path)),
            )
            if len(paths) > self.path_budget:
                partial_reasons.add("evidence_path_budget")
                paths = paths[: self.path_budget]
            if paths:
                best = paths[0]
                evidence = sorted(
                    {t.tx_hash: t for p in paths for t in p.path}.values(), key=lambda t: t.order
                )
                hits.append(
                    self.hit(
                        "RAPID_HIGH_RISK",
                        best.source["address"],
                        evidence,
                        {
                            "allocated_base": str(sum(p.amount for p in paths)),
                            "source": best.source,
                            "hops": len(best.path),
                            "paths": [
                                {
                                    "tx_hashes": [t.tx_hash for t in p.path],
                                    "allocated_base": str(p.amount),
                                    "source": p.source,
                                    "hops": len(p.path),
                                }
                                for p in paths
                            ],
                        },
                        {"hops": [3, 5], "hold_seconds_max": 120, "forward_ratio_min": 0.8},
                    )
                )
        codes = [h["code"] for h in hits]
        out5 = self.window(tx.sender, tx, 300)
        in5 = self.window(tx.recipient, tx, 300, True)
        volume_out = sum(t.value for t, _ in self.window(tx.sender, tx, 86400))
        volume_in = sum(t.value for t, _ in self.window(tx.recipient, tx, 86400, True))
        direct_component = max((s["severity"] / 100 * s["confidence"] for s, _ in direct), default=0)
        result = score(
            {
                "E": min(1, max(exposure, direct_component)),
                "P": float(any(c in {"STRUCTURING", "SMURFING", "PEELING", "MIXING_PATTERN", "CIRCULAR_FLOW"} for c in codes)),
                "V": min(1, max(len(out5), len(in5)) / 20),
                "Q": min(1, max(volume_out, volume_in) / (3 * THRESHOLD)),
                "A": 0.0,
            },
            codes,
        )
        result.update(
            {
                "hits": hits,
                "coverage": "partial" if partial_reasons else "complete",
                "partial_reasons": sorted(partial_reasons),
                "truncated": bool({"graph_budget", "evidence_path_budget"} & partial_reasons),
                "as_of_order": list(tx.order),
                "detected_at": tx.timestamp.isoformat(),
                "analysis_mode": self.mode,
            }
        )
        if tx.sender != tx.recipient:
            self.last_in[tx.recipient] = tx.global_index
        return result

    @staticmethod
    def hit(code, anchor, evidence, observed, thresholds):
        return {
            "code": code,
            "anchor": anchor,
            "evidence": list(dict.fromkeys(t.tx_hash for t in evidence)),
            "observed": observed,
            "thresholds": thresholds,
            "rule_version": "rules-v1",
        }

    def structuring(self, tx):
        outs = [t for t, _ in self.window(tx.sender, tx, 1800) if t.sender != t.recipient]
        near = [t for t in outs if THRESHOLD * 8 <= t.value * 10 and t.value < THRESHOLD]
        total = sum(t.value for t in near)
        if len(near) >= 8 and total >= 5 * THRESHOLD and len(near) * 10 >= len(outs) * 7:
            return [
                self.hit(
                    "STRUCTURING",
                    tx.sender,
                    near,
                    {
                        "count": len(near),
                        "sum_base": str(total),
                        "near_threshold_ratio": len(near) / len(outs),
                    },
                    {
                        "count_min": 8,
                        "sum_base_min": str(5 * THRESHOLD),
                        "ratio_min": 0.7,
                        "T_base": str(THRESHOLD),
                        "window_seconds": 1800,
                    },
                )
            ]
        return []

    def smurfing(self, tx, partial):
        groups = defaultdict(lambda: {"amount": 0, "intermediates": set(), "txs": {}, "paths": set()})
        visited = 0
        for out_tx, portions in self.flows[tx.recipient]:
            if out_tx.timestamp < tx.timestamp - timedelta(hours=1):
                continue
            for p in portions:
                visited += 1
                if visited > self.edge_budget:
                    partial.add("graph_budget")
                    break
                for hops in (2, 3):
                    path = p.path[-hops:]
                    if len(path) < hops or path[0].timestamp < tx.timestamp - timedelta(hours=1):
                        continue
                    nodes = [path[0].sender] + [t.recipient for t in path]
                    if len(nodes) != len(set(nodes)):
                        continue
                    group = groups[path[0].sender]
                    path_key = tuple(t.tx_hash for t in path)
                    if path_key not in group["paths"] and len(group["paths"]) >= self.path_budget:
                        partial.add("evidence_path_budget")
                        continue
                    group["paths"].add(path_key)
                    # A portion is assigned only once per candidate anchor at this withdrawal.
                    if hops == 3 and len(p.path) >= 2 and path[0].sender == p.path[-2].sender:
                        continue
                    group["amount"] += p.amount
                    group["intermediates"].add(path[0].recipient)
                    group["txs"].update({t.tx_hash: t for t in path})
            if visited > self.edge_budget:
                break
        hits = []
        for anchor, group in groups.items():
            outgoing = [t for t, _ in self.window(anchor, tx, 3600) if t.sender != t.recipient]
            total = sum(t.value for t in outgoing)
            if len(group["intermediates"]) >= 8 and total and group["amount"] * 10 >= total * 7:
                evidence = sorted(group["txs"].values(), key=lambda t: t.order)
                hits.append(
                    self.hit(
                        "SMURFING",
                        anchor + ":" + tx.recipient,
                        evidence,
                        {
                            "intermediates": len(group["intermediates"]),
                            "allocated_base": str(min(group["amount"], total)),
                            "ratio": min(1, group["amount"] / total),
                        },
                        {"intermediates_min": 8, "ratio_min": 0.7, "hops_max": 3, "window_seconds": 3600},
                    )
                )
        return hits

    def peeling(self, tx):
        entries = [
            (t, b)
            for t, b in self.window(tx.sender, tx, 600)
            if t.global_index > self.last_in.get(tx.sender, -1) and t.sender != t.recipient
        ]
        chain = self.peel.get(tx.sender, (0, []))
        if chain[1] and entries and (entries[0][0].timestamp - chain[1][-1].timestamp).total_seconds() > 600:
            chain = (0, [])
        self.peel.pop(tx.recipient, None)
        if len(entries) < 2:
            return []
        start_balance = entries[0][1]
        small = [t for t, _ in entries[:-1]]
        small_value = sum(t.value for t in small)
        if (
            start_balance
            and start_balance * 80 <= tx.value * 100 <= start_balance * 99
            and start_balance <= small_value * 100 <= start_balance * 20
            and self.ledger.balances[tx.sender] * 100 <= start_balance * 2
        ):
            level = chain[0] + 1
            evidence = chain[1] + small + [tx]
            self.peel[tx.recipient] = (level, evidence)
            if level >= 4:
                return [
                    self.hit(
                        "PEELING",
                        evidence[0].sender,
                        evidence,
                        {
                            "levels": level,
                            "start_balance_base": str(start_balance),
                            "continuation_base": str(tx.value),
                            "peeled_base": str(small_value),
                            "remaining_base": str(self.ledger.balances[tx.sender]),
                        },
                        {
                            "levels_min": 4,
                            "continuation_ratio": [0.8, 0.99],
                            "peel_ratio": [0.01, 0.2],
                            "remaining_ratio_max": 0.02,
                        },
                    )
                ]
        return []

    def mixing(self, tx, partial):
        for address in {tx.sender, tx.recipient}:
            ins = {t.sender for t, _ in self.window(address, tx, 1800, True) if t.sender != t.recipient}
            outs = {t.recipient for t, _ in self.window(address, tx, 1800) if t.sender != t.recipient}
            if len(ins) >= 3 and len(outs) >= 3:
                self.candidates.add(address)
            else:
                self.candidates.discard(address)
        if tx.sender not in self.candidates:
            return []
        region = {tx.sender}
        frontier = [tx.sender]
        while frontier:
            a = frontier.pop()
            neighbors = {node[1] for node in self.graph.predecessors(self.node(a))} | {
                node[1] for node in self.graph.successors(self.node(a))
            }
            new = (neighbors & self.candidates) - region
            region.update(new)
            frontier.extend(new)
            if len(region) > 100:
                partial.add("graph_budget")
                return []
        if tx.recipient in region:
            return []
        deposit, withdrawal, fees = {}, {}, {}
        visited = 0
        lower = tx.timestamp - timedelta(minutes=30)
        for a in region:
            for _, _, key, data in list(self.graph.in_edges(self.node(a), keys=True, data=True)) + list(
                self.graph.out_edges(self.node(a), keys=True, data=True)
            ):
                visited += 1
                if visited > self.edge_budget:
                    partial.add("graph_budget")
                    return []
                t = data["tx"]
                if t.timestamp < lower:
                    continue
                if t.sender not in region and t.recipient in region:
                    deposit[key] = t
                if t.sender in region and t.recipient not in region:
                    withdrawal[key] = t
                if t.sender in region:
                    fees[key] = t
        sources = {t.sender for t in deposit.values()}
        targets = {t.recipient for t in withdrawal.values()}
        inflow = sum(t.value for t in deposit.values())
        outflow = sum(t.value for t in withdrawal.values())
        if (
            len(sources) < 5
            or len(targets) < 5
            or inflow < 5 * THRESHOLD
            or not 85 * inflow <= 100 * outflow <= 105 * inflow
        ):
            return []
        outputs = defaultdict(int)
        for t in withdrawal.values():
            outputs[t.recipient] += t.value
        entropy = -sum((v / outflow) * math.log(v / outflow) for v in outputs.values()) / math.log(
            len(outputs)
        )
        fast = 0
        for target in targets:
            for out_tx, portions in self.flows[target]:
                if out_tx.tx_hash not in withdrawal:
                    continue
                for p in portions:
                    entry = next((t for t in p.path if t.tx_hash in deposit), None)
                    if entry and 0 <= (out_tx.timestamp - entry.timestamp).total_seconds() <= 600:
                        fast += p.amount
        if entropy < 0.8 or fast * 10 < inflow * 7:
            return []
        final_balance = sum(self.ledger.balances[a] for a in region)
        fee_sum = sum(t.fee for t in fees.values())
        initial_balance = sum(
            self.ledger.balances[a]
            - sum(
                delta
                for t, delta in self.balance_changes[a]
                if lower <= t.timestamp <= tx.timestamp and t.order <= tx.order
            )
            for a in region
        )
        if initial_balance + inflow != final_balance + outflow + fee_sum:
            raise AssertionError(
                "Temporal graph boundary flow disagrees with independent ledger balance history"
            )
        evidence = sorted({**deposit, **fees, **withdrawal}.values(), key=lambda t: t.order)
        return [
            self.hit(
                "MIXING_PATTERN",
                canonical_hash(sorted(region)),
                evidence,
                {
                    "region": sorted(region),
                    "sources": len(sources),
                    "destinations": len(targets),
                    "inflow_base": str(inflow),
                    "outflow_base": str(outflow),
                    "fee_base": str(fee_sum),
                    "balance_start_base": str(initial_balance),
                    "balance_end_base": str(final_balance),
                    "fast_allocated_base": str(fast),
                    "entropy": entropy,
                },
                {
                    "sources_min": 5,
                    "destinations_min": 5,
                    "inflow_min_base": str(5 * THRESHOLD),
                    "output_ratio": [0.85, 1.05],
                    "fast_ratio_min": 0.7,
                    "entropy_min": 0.8,
                    "window_seconds": 1800,
                },
            )
        ]

    def circular_flow(self, tx: Tx, moved: list[Part]) -> list[dict]:
        candidates = {}
        for p in moved:
            if len(p.path) < 3:
                continue
            for start_idx in range(len(p.path) - 2):
                cycle = p.path[start_idx:]
                if cycle[0].sender == tx.recipient and 3 <= len(cycle) <= 5:
                    intermediates = [t.recipient for t in cycle[:-1]]
                    if len(set(intermediates)) != len(intermediates):
                        continue
                    if tx.recipient in intermediates:
                        continue
                    if (tx.timestamp - cycle[0].timestamp).total_seconds() > 3600:
                        continue
                    if cycle[0].value < 100 * UNIT:
                        continue
                    key = (cycle[0].sender, tuple(t.tx_hash for t in cycle))
                    if key not in candidates:
                        candidates[key] = [cycle, p.amount]
                    else:
                        candidates[key][1] += p.amount
                    break
        hits = []
        for (anchor, hashes), (cycle, total_returned) in candidates.items():
            if total_returned * 10 < cycle[0].value * 7:
                continue
            hits.append(
                self.hit(
                    "CIRCULAR_FLOW",
                    anchor,
                    list(cycle),
                    {
                        "anchor": anchor,
                        "hops": len(cycle),
                        "initial_value_base": str(cycle[0].value),
                        "returned_value_base": str(total_returned),
                        "retention_ratio": min(1.0, total_returned / cycle[0].value),
                        "duration_seconds": (tx.timestamp - cycle[0].timestamp).total_seconds(),
                        "path_tx_hashes": list(hashes),
                    },
                    {
                        "hops_min": 3,
                        "hops_max": 5,
                        "retention_ratio_min": 0.7,
                        "window_seconds": 3600,
                        "min_value_base": str(100 * UNIT),
                    },
                )
            )
        return hits
