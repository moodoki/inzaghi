from datetime import datetime

import pytest

from inzaghi import parse


@pytest.mark.parametrize(
    "name, kind, slug",
    [
        ("2026-09-04_2325_milestone_shard-2-reindexed.md", "milestone", "shard-2-reindexed"),
        ("2026-09-04_2302_phase-summary_stage-1-summary.md", "phase-summary", "stage-1-summary"),
        ("2026-09-04_0112_hard-stop_needs-a-decision.md", "hard-stop", "needs-a-decision"),
    ],
)
def test_event_filename_yields_kind_and_slug(name, kind, slug):
    parsed = parse.parse_event_filename(name)
    assert (parsed.kind, parsed.slug) == (kind, slug)
    assert parsed.ts is not None


def test_moved_inbox_filename_is_not_mistaken_for_a_kind():
    """A delivered message is stamped, but the rest of its name is not a kind."""
    parsed = parse.parse_event_filename("2026-09-04_2304_2026-09-04-heartbeat.md")
    assert parsed.kind is None
    assert parsed.slug == "2026-09-04-heartbeat"
    assert parsed.ts == datetime(2026, 9, 4, 23, 4).astimezone()


def test_unconventional_filename_still_parses():
    parsed = parse.parse_event_filename("notes.txt")
    assert (parsed.ts, parsed.kind, parsed.slug) == (None, None, "notes")


@pytest.mark.parametrize(
    "left, right",
    [
        ("2026-09-04-heartbeat.md", "re: 2026-09-04-heartbeat.md"),
        ("2026-09-04-heartbeat.md", "re-2026-09-04-heartbeat-md"),
        ("2026-09-04_2304_pause-gpu.md", "pause-gpu"),
        # What the app itself produces: Inzaghi stamps the message, then the
        # session stamps the pickup time onto that when it moves the file.
        ("2026-09-05_1200_2026-09-05_1130_pause.md", "re: 2026-09-05_1130_pause.md"),
        ("2026-09-05_1200_2026-09-05_1130_pause.md", "2026-09-05_1130_pause.md"),
    ],
)
def test_refs_normalise_to_the_same_key(left, right):
    assert parse.normalise_ref(left) == parse.normalise_ref(right)


@pytest.mark.parametrize(
    "name",
    [
        "STATUS (conflicted copy 2026-09-05).md",
        "STATUS (alex's conflicted copy 2026-09-05).md",
        "STATUS.sync-conflict-20260905-that-box.md",
    ],
)
def test_conflict_copies_are_recognised(name):
    assert parse.is_conflict_copy(name)


def test_ordinary_names_are_not_conflicts():
    assert not parse.is_conflict_copy("2026-09-04_2325_milestone_shard-2.md")


def test_timestamp_accepts_offsets_with_and_without_a_colon():
    assert parse.parse_timestamp("at 2026-09-05T05:57:46+0800 ok") == parse.parse_timestamp(
        "at 2026-09-05T05:57:46+08:00 ok"
    )


def test_timestamp_without_a_zone_is_localised():
    assert parse.parse_timestamp("2026-09-05 05:57").tzinfo is not None


def test_front_matter_is_optional_and_flat():
    meta, body = parse.split_front_matter("---\nkind: error\nts: 2026-09-05\n---\n# Title\n")
    assert meta == {"kind": "error", "ts": "2026-09-05"}
    assert body.startswith("# Title")


def test_document_without_front_matter_is_untouched():
    text = "# Title\n\nbody\n"
    assert parse.split_front_matter(text) == ({}, text)


def test_horizontal_rule_is_not_front_matter():
    text = "---\n\njust a rule, not a header\n"
    assert parse.split_front_matter(text)[1] == text


def test_kv_bullets_and_sections():
    text = "- **updated:** now\n\n## Waiting on you\nNothing.\n"
    assert parse.parse_kv_bullets(text)["updated"] == "now"
    assert parse.find_section(text, "waiting on you") == "Nothing."


def test_slugify_is_filename_safe():
    assert parse.slugify("Pause the GPU jobs, please!") == "pause-the-gpu-jobs-please"
    assert parse.slugify("!!!") == "message"


def test_first_sentence_stops_at_the_first_full_stop_or_line_break():
    assert parse.first_sentence("Nothing. Phase 3c is running.") == "Nothing"
    assert parse.first_sentence("Nothing blocking\n\nBut decide the batch size.") == (
        "Nothing blocking"
    )
    assert parse.first_sentence("Which checkpoint?") == "Which checkpoint"


def test_first_sentence_drops_what_prose_wraps_a_word_in():
    """A session writing `- **Nothing.**` is saying the same word as one
    writing `Nothing`."""
    for written in ("**Nothing.**", "- Nothing.", "*Nothing*", "`Nothing`", "  Nothing  "):
        assert parse.first_sentence(written) == "Nothing", written


def test_first_sentence_of_nothing_is_nothing():
    assert parse.first_sentence("") == ""
    assert parse.first_sentence("N/A") == "N/A"
