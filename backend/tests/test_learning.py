from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.services.learning import mastery, roadmap


@pytest.mark.parametrize(
    "events,level,score",
    [
        ([], "unknown", None),
        ([(1, 1)], "developing", Decimal(2) / 3),
        ([(1, 1)] * 3, "mastered", Decimal(".8")),
        ([(0, 1)], "reinforce", Decimal(1) / 3),
    ],
)
def test_DAT_20_mastery(events, level, score):
    result = mastery(events)
    assert result["level"] == level
    assert result["masteryScore"] == score


@given(st.lists(st.tuples(st.integers(0, 1000), st.integers(1, 100)), max_size=100))
@settings(max_examples=100)
def test_mastery_bounds_and_rebuild(values):
    events = [(Decimal(v) / 1000, Decimal(w) / 100) for v, w in values]
    a, b = mastery(events), mastery(list(reversed(events)))
    assert a == b
    assert a["alpha"] >= 1 and a["beta"] >= 1
    if events:
        assert 0 < a["masteryScore"] < 1


def node(key, level="unknown", source=True):
    return dict(id=key, canonical_key=key, level=level, estimated_minutes=20, has_source=source)


def test_DAT_21_prerequisite_closure_and_stable_ties():
    nodes = [node("a", "mastered"), node("b", "reinforce"), node("c")]
    steps = roadmap(nodes, [("a", "b"), ("b", "c")], ["c"])
    assert [s["conceptId"] for s in steps] == ["a", "b", "c"]
    assert steps[0]["state"] == "completed"
    assert steps == roadmap(list(reversed(nodes)), [("b", "c"), ("a", "b")], ["c"])


def test_invalid_cycle_and_missing_prerequisite():
    with pytest.raises(ValueError, match="INVALID_GRAPH"):
        roadmap([node("a"), node("b")], [("a", "b"), ("b", "a")], ["b"])
    with pytest.raises(ValueError, match="INVALID_GRAPH"):
        roadmap([node("a")], [], ["missing"])
    assert roadmap([node("a", source=False)], [], ["a"])[0]["reason"] == "NO_SOURCE"


@given(st.integers(1, 60))
@settings(max_examples=100)
def test_topological_chain_never_omits_ancestors(n):
    nodes = [node(str(i)) for i in range(n)]
    edges = [(str(i), str(i + 1)) for i in range(n - 1)]
    steps = roadmap(nodes, edges, [str(n - 1)])
    assert [s["conceptId"] for s in steps] == [str(i) for i in range(n)]


def test_retired_prerequisite_remains_blocked_even_if_previously_mastered():
    retired = {**node("a", "mastered"), "status": "retired"}
    steps = roadmap([retired, node("b")], [("a", "b")], ["b"])
    assert [step["conceptId"] for step in steps] == ["a", "b"]
    assert steps[0]["state"] == "blocked" and steps[0]["reason"] == "RETIRED_CONCEPT"
