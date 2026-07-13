from __future__ import annotations

import asyncio

import numpy as np

from agents import labeller


# ---------------------------------------------------------------------------
# parse_label_response — kept unchanged
# ---------------------------------------------------------------------------

def test_parse_plain_json():
    out = labeller.parse_label_response(
        '{"label": "Login broken", "category": "bug", "sentiment": "negative", "summary": "x"}'
    )
    assert out["label"] == "Login broken"
    assert out["category"] == "bug"


def test_parse_fenced_markdown():
    fenced = (
        "```json\n"
        '{"label": "Great dark mode", "category": "praise", '
        '"sentiment": "positive", "summary": "users love it"}\n'
        "```"
    )
    out = labeller.parse_label_response(fenced)
    assert out is not None
    assert out["category"] == "praise"
    assert out["sentiment"] == "positive"


def test_parse_malformed_returns_none():
    assert labeller.parse_label_response("not json at all") is None
    assert labeller.parse_label_response('{"label": "x"}') is None  # missing keys


def test_parse_bad_category_falls_back_to_other():
    out = labeller.parse_label_response(
        '{"label": "x", "category": "banana", "sentiment": "neutral", "summary": "y"}'
    )
    assert out["category"] == "other"


# ---------------------------------------------------------------------------
# parse_dedupe_response — pure function, no LLM
# ---------------------------------------------------------------------------

def test_parse_dedupe_plain_json_valid_members():
    text = (
        '{"themes": ['
        '{"canonical_label": "App Crashes", "category": "bug", "member_cluster_ids": ["cluster_0", "cluster_1"]},'
        '{"canonical_label": "Great Design", "category": "praise", "member_cluster_ids": ["cluster_2"]}'
        ']}'
    )
    valid = {"cluster_0", "cluster_1", "cluster_2"}
    result = labeller.parse_dedupe_response(text, valid)
    assert result is not None
    assert len(result) == 2
    labels = [t["canonical_label"] for t in result]
    assert "App Crashes" in labels
    assert "Great Design" in labels


def test_parse_dedupe_drops_hallucinated_member_ids():
    # cluster_99 does not exist in valid_cids
    text = (
        '{"themes": ['
        '{"canonical_label": "Crashes", "category": "bug", '
        '"member_cluster_ids": ["cluster_0", "cluster_99"]}'
        ']}'
    )
    valid = {"cluster_0", "cluster_1"}
    result = labeller.parse_dedupe_response(text, valid)
    assert result is not None
    assert result[0]["member_cluster_ids"] == ["cluster_0"]


def test_parse_dedupe_drops_theme_with_no_valid_members():
    # All members are hallucinated -> theme is dropped entirely
    text = (
        '{"themes": ['
        '{"canonical_label": "Ghost", "category": "bug", '
        '"member_cluster_ids": ["cluster_99", "cluster_100"]},'
        '{"canonical_label": "Real Theme", "category": "praise", '
        '"member_cluster_ids": ["cluster_0"]}'
        ']}'
    )
    valid = {"cluster_0"}
    result = labeller.parse_dedupe_response(text, valid)
    assert result is not None
    assert len(result) == 1
    assert result[0]["canonical_label"] == "Real Theme"


def test_parse_dedupe_bad_category_becomes_other():
    text = (
        '{"themes": ['
        '{"canonical_label": "Weird thing", "category": "nonsense", '
        '"member_cluster_ids": ["cluster_0"]}'
        ']}'
    )
    result = labeller.parse_dedupe_response(text, {"cluster_0"})
    assert result is not None
    assert result[0]["category"] == "other"


def test_parse_dedupe_fenced_markdown():
    text = (
        "```json\n"
        '{"themes": [{"canonical_label": "Login Bug", "category": "bug", '
        '"member_cluster_ids": ["cluster_0"]}]}\n'
        "```"
    )
    result = labeller.parse_dedupe_response(text, {"cluster_0"})
    assert result is not None
    assert result[0]["canonical_label"] == "Login Bug"


def test_parse_dedupe_malformed_returns_none():
    assert labeller.parse_dedupe_response("not json", {"cluster_0"}) is None


def test_parse_dedupe_missing_themes_key_returns_none():
    # Valid JSON but wrong shape
    assert labeller.parse_dedupe_response('{"data": []}', {"cluster_0"}) is None


def test_parse_dedupe_empty_themes_list_returns_none():
    # themes key exists but is empty -> nothing cleaned -> None
    assert labeller.parse_dedupe_response('{"themes": []}', {"cluster_0"}) is None


# ---------------------------------------------------------------------------
# dedupe_clusters — async, mocked Anthropic client (same fakes as existing tests)
# ---------------------------------------------------------------------------

class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Resp:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


def _make_themes_json(themes: list[dict]) -> str:
    import json
    return json.dumps({"themes": themes})


def _make_client_with_responses(*responses: str):
    """Returns a fake client whose messages.create returns each response in order."""
    queue = list(responses)

    class _Messages:
        async def create(self, **_kw):
            return _Resp(queue.pop(0))

    class _Client:
        messages = _Messages()

    return _Client()


async def test_dedupe_happy_path_merges_into_larger_canonical():
    """3 clusters -> 2 themes; small cluster folds into the largest member."""
    labels = {
        "cluster_0": {"label": "App Crashes", "category": "bug", "sentiment": "negative", "summary": "crashes often"},
        "cluster_1": {"label": "Crashes Always", "category": "bug", "sentiment": "negative", "summary": "always crashing"},
        "cluster_2": {"label": "Love Design", "category": "praise", "sentiment": "positive", "summary": "looks good"},
    }
    counts = {"cluster_0": 30, "cluster_1": 10, "cluster_2": 20}

    # LLM groups cluster_0 + cluster_1 into one theme; cluster_2 alone
    themes_json = _make_themes_json([
        {"canonical_label": "App Crashes", "category": "bug", "member_cluster_ids": ["cluster_0", "cluster_1"]},
        {"canonical_label": "Love Design", "category": "praise", "member_cluster_ids": ["cluster_2"]},
    ])
    client = _make_client_with_responses(themes_json)

    merge_map, merged_meta = await labeller.dedupe_clusters(client, labels, counts)

    # cluster_0 is the largest (30 vs 10) -> canonical
    assert merge_map["cluster_0"] == "cluster_0"
    assert merge_map["cluster_1"] == "cluster_0"
    assert merge_map["cluster_2"] == "cluster_2"

    # Two themes survive
    assert set(merged_meta) == {"cluster_0", "cluster_2"}

    # merged theme uses the new canonical_label from the dedupe response
    assert merged_meta["cluster_0"]["label"] == "App Crashes"
    # sentiment + summary come from canonical cluster's original label dict, not the LLM
    assert merged_meta["cluster_0"]["sentiment"] == labels["cluster_0"]["sentiment"]
    assert merged_meta["cluster_0"]["summary"] == labels["cluster_0"]["summary"]


async def test_dedupe_retry_first_malformed_second_valid():
    """First response is garbage; second is valid -> succeeds, exactly 2 calls made."""
    labels = {
        "cluster_0": {"label": "Bug", "category": "bug", "sentiment": "negative", "summary": "s"},
    }
    counts = {"cluster_0": 5}
    call_count = {"n": 0}

    valid_json = _make_themes_json([
        {"canonical_label": "Bug", "category": "bug", "member_cluster_ids": ["cluster_0"]},
    ])

    class _Messages:
        async def create(self, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _Resp("garbage not json at all")
            return _Resp(valid_json)

    class _Client:
        messages = _Messages()

    merge_map, merged_meta = await labeller.dedupe_clusters(_Client(), labels, counts)

    assert call_count["n"] == 2
    assert merge_map["cluster_0"] == "cluster_0"
    assert "cluster_0" in merged_meta


async def test_dedupe_fallback_when_both_responses_malformed():
    """Both responses are garbage -> identity map, no exception raised."""
    labels = {
        "cluster_0": {"label": "Bug", "category": "bug", "sentiment": "negative", "summary": "s"},
        "cluster_1": {"label": "UX", "category": "ux", "sentiment": "mixed", "summary": "t"},
    }
    counts = {"cluster_0": 10, "cluster_1": 5}
    client = _make_client_with_responses("bad", "also bad")

    merge_map, merged_meta = await labeller.dedupe_clusters(client, labels, counts)

    # Identity map: every cluster maps to itself
    assert merge_map == {"cluster_0": "cluster_0", "cluster_1": "cluster_1"}
    # Original label dicts preserved
    assert merged_meta["cluster_0"] == labels["cluster_0"]
    assert merged_meta["cluster_1"] == labels["cluster_1"]


async def test_dedupe_omitted_cluster_survives_as_own_theme():
    """LLM only mentions cluster_0; cluster_1 is omitted but must not disappear."""
    labels = {
        "cluster_0": {"label": "Crash", "category": "bug", "sentiment": "negative", "summary": "crashes"},
        "cluster_1": {"label": "Pricing", "category": "pricing", "sentiment": "negative", "summary": "too expensive"},
    }
    counts = {"cluster_0": 20, "cluster_1": 15}

    # LLM response only covers cluster_0
    themes_json = _make_themes_json([
        {"canonical_label": "Crash", "category": "bug", "member_cluster_ids": ["cluster_0"]},
    ])
    client = _make_client_with_responses(themes_json)

    merge_map, merged_meta = await labeller.dedupe_clusters(client, labels, counts)

    # Both clusters appear in the output
    assert "cluster_0" in merge_map
    assert "cluster_1" in merge_map
    assert "cluster_0" in merged_meta
    assert "cluster_1" in merged_meta
    # Omitted cluster is its own canonical
    assert merge_map["cluster_1"] == "cluster_1"
    # Omitted cluster preserves its original label dict
    assert merged_meta["cluster_1"] == labels["cluster_1"]


# ---------------------------------------------------------------------------
# _label_all — semaphore-bounded concurrency (kept unchanged)
# ---------------------------------------------------------------------------

async def test_label_all_respects_concurrency(monkeypatch):
    active = 0
    peak = 0

    async def fake_label(_client, _texts):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"label": "x", "category": "other", "sentiment": "neutral", "summary": ""}, None

    monkeypatch.setattr(labeller, "_label_cluster", fake_label)

    samples = {f"cluster_{i}": ["text"] for i in range(20)}
    out = await labeller._label_all(None, samples, concurrency=5)

    assert len(out) == 20
    assert peak <= 5  # never more than the semaphore allows in flight


# ---------------------------------------------------------------------------
# _label_cluster — retry behaviour (kept unchanged)
# ---------------------------------------------------------------------------

async def test_label_cluster_retries_then_parses():
    calls = {"n": 0}

    class _Block:
        type = "text"

        def __init__(self, text):
            self.text = text

    class _Resp:
        def __init__(self, text):
            self.content = [_Block(text)]

    class _Messages:
        async def create(self, **_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp("garbage not json")
            return _Resp('{"label": "Sync fails", "category": "bug", "sentiment": "negative", "summary": "s"}')

    class _Client:
        messages = _Messages()

    parsed, err = await labeller._label_cluster(_Client(), ["a", "b"])
    assert err is None
    assert parsed["label"] == "Sync fails"
    assert calls["n"] == 2  # first malformed, retried once


# ---------------------------------------------------------------------------
# parse_dedupe_response — duplicate cluster_id dedup (P0 regression)
# ---------------------------------------------------------------------------

def test_parse_dedupe_duplicate_cluster_id_first_theme_wins():
    """Regression: same cluster_id in two themes must end up in exactly one theme.
    The first theme to claim it keeps it; the second loses it. If the second
    theme is left with no members it is dropped entirely."""
    text = (
        '{"themes": ['
        '{"canonical_label": "App Crashes", "category": "bug", '
        '"member_cluster_ids": ["cluster_0", "cluster_1"]},'
        '{"canonical_label": "Crash Duplicate", "category": "bug", '
        '"member_cluster_ids": ["cluster_1", "cluster_2"]}'
        ']}'
    )
    valid = {"cluster_0", "cluster_1", "cluster_2"}
    result = labeller.parse_dedupe_response(text, valid)

    assert result is not None

    # Flatten all member_cluster_ids across every theme
    all_members = [cid for theme in result for cid in theme["member_cluster_ids"]]

    # No duplicates anywhere in the output
    assert len(all_members) == len(set(all_members)), (
        f"Duplicate cluster_ids found across themes: {all_members}"
    )

    # cluster_1 was claimed first by theme[0] — it must stay there
    first_theme = result[0]
    assert first_theme["canonical_label"] == "App Crashes"
    assert "cluster_1" in first_theme["member_cluster_ids"]

    # cluster_1 must NOT appear in any subsequent theme
    for theme in result[1:]:
        assert "cluster_1" not in theme["member_cluster_ids"], (
            f"cluster_1 wrongly duplicated into theme '{theme['canonical_label']}'"
        )
