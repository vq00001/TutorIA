"""Deterministic beta_v1 and toposort_v1, independent of the AI provider."""

from decimal import Decimal
from heapq import heappop, heappush

MASTERY_VERSION = "beta_v1"
ROADMAP_VERSION = "toposort_v1"
ROADMAP_PARAMETERS = {
    "level_order": ["reinforce", "developing", "unknown", "mastered"],
    "tie_breakers": ["canonical_key", "id"],
    "dependencies": ["prerequisite"],
}


def mastery(events):
    alpha = beta = Decimal(1)
    count = 0
    for value, weight in events:
        value, weight = Decimal(str(value)), Decimal(str(weight))
        if not 0 <= value <= 1 or weight <= 0:
            raise ValueError("INVALID_EVIDENCE")
        alpha += value * weight
        beta += (1 - value) * weight
        count += 1
    score = alpha / (alpha + beta)
    level = (
        "unknown"
        if count == 0
        else "mastered"
        if score >= Decimal(".80") and count >= 3
        else "reinforce"
        if score < Decimal(".60")
        else "developing"
    )
    return dict(
        alpha=alpha,
        beta=beta,
        evidenceCount=count,
        level=level,
        masteryScore=score if count else None,
        algorithmVersion=MASTERY_VERSION,
    )


def roadmap(concepts, edges, goals):
    """Include the prerequisite closure; order only approved prerequisite edges."""
    by_id = {str(c["id"]): c for c in concepts}
    parents = {cid: set() for cid in by_id}
    children = {cid: set() for cid in by_id}
    for a, b in edges:
        a, b = str(a), str(b)
        if a not in by_id or b not in by_id:
            raise ValueError("INVALID_GRAPH")
        parents[b].add(a)
        children[a].add(b)
    required, pending = set(), list(map(str, goals))
    while pending:
        cid = pending.pop()
        if cid not in by_id:
            raise ValueError("INVALID_GRAPH")
        if cid not in required:
            required.add(cid)
            pending.extend(parents[cid])
    rank = {level: index for index, level in enumerate(ROADMAP_PARAMETERS["level_order"])}
    degrees = {cid: len(parents[cid] & required) for cid in required}
    heap = []

    def push(cid):
        c = by_id[cid]
        heappush(heap, (rank[c.get("level", "unknown")], c["canonical_key"], cid))

    for cid, degree in degrees.items():
        if not degree:
            push(cid)
    result = []
    while heap:
        _, _, cid = heappop(heap)
        c = by_id[cid]
        state = (
            "blocked"
            if c.get("status", "approved") != "approved"
            else "completed"
            if c.get("level") == "mastered"
            else "blocked"
            if not c.get("has_source", False)
            else "pending"
        )
        result.append(
            dict(
                conceptId=cid,
                position=len(result) + 1,
                state=state,
                estimatedMinutes=c["estimated_minutes"],
                reason="RETIRED_CONCEPT"
                if c.get("status") == "retired"
                else "NO_SOURCE"
                if state == "blocked"
                else "prerequisite_order",
            )
        )
        for child in children[cid] & required:
            degrees[child] -= 1
            if not degrees[child]:
                push(child)
    if len(result) != len(required):
        raise ValueError("INVALID_GRAPH")
    return result
