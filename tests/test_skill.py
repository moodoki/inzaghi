"""The skill that teaches a session how to operate a channel."""

from __future__ import annotations

from pathlib import Path

import pytest

from inzaghi import skill
from inzaghi.protocol import CHANNEL_README, PROTOCOL_VERSION


def test_the_bundled_contract_has_not_drifted_from_protocol_py():
    """One source of truth; run `inz skill sync` if this fails."""
    assert skill.REFERENCE.read_text(encoding="utf-8") == skill.render_reference()


def test_sync_is_idempotent():
    assert skill.sync_reference() is False


def test_the_contract_names_the_protocol_version():
    assert f"Protocol v{PROTOCOL_VERSION}" in skill.render_reference()
    assert CHANNEL_README.count("{name}") == skill.render_reference().count(skill.SAMPLE_NAME)


def test_the_skill_declares_the_front_matter_a_harness_needs():
    text = (skill.SOURCE / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    header = text.split("---", 2)[1]
    assert "name: inzaghi" in header
    assert "description:" in header


def test_the_skill_covers_the_parts_a_session_gets_wrong():
    text = (skill.SOURCE / "SKILL.md").read_text(encoding="utf-8").lower()
    for topic in ("heartbeat", "waiting on you", "atomically", "hard-stop", "inbox/done", "mtime"):
        assert topic in text, f"the skill never mentions {topic}"


# -- installing -----------------------------------------------------------


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """A harness whose user directory is inside the test's tmp_path."""
    fake = skill.Harness(
        name="claude-code",
        user_dir=tmp_path / "home" / ".claude" / "skills",
        project_dir=".claude/skills",
        layout="test",
    )
    monkeypatch.setitem(skill.HARNESSES, "claude-code", fake)
    return fake


def test_installing_links_by_default(harness):
    path, how = skill.install()
    assert how == "linked"
    assert path.is_symlink()
    assert (path / "SKILL.md").is_file()
    assert (path / "reference" / "channel-README.md").is_file()


def test_a_linked_skill_tracks_edits_to_the_source(harness):
    path, _ = skill.install()
    assert (path / "SKILL.md").read_text() == (skill.SOURCE / "SKILL.md").read_text()


def test_copying_detaches_from_the_source(harness):
    path, how = skill.install(link=False)
    assert how == "copied"
    assert not path.is_symlink() and (path / "SKILL.md").is_file()


def test_installing_for_one_project_only(harness, tmp_path):
    project = tmp_path / "someproject"
    path, _ = skill.install(project=project)
    assert path == project / ".claude" / "skills" / "inzaghi"
    assert (path / "SKILL.md").is_file()


def test_an_existing_install_is_not_clobbered(harness):
    skill.install()
    with pytest.raises(FileExistsError):
        skill.install()


def test_force_replaces_a_previous_install(harness):
    first, _ = skill.install()
    second, how = skill.install(link=False, force=True)
    assert first == second and how == "copied"
    assert not second.is_symlink()


def test_force_refuses_to_delete_something_that_is_not_a_skill(harness):
    """--force replaces an install; it is not a way to remove a stray directory."""
    destination = harness.destination()
    destination.mkdir(parents=True)
    (destination / "important.txt").write_text("not ours")
    with pytest.raises(FileExistsError):
        skill.install(force=True)
    assert (destination / "important.txt").exists()


def test_an_unknown_harness_is_rejected():
    with pytest.raises(skill.UnknownHarness):
        skill.install("opencode")


def test_adding_a_harness_is_one_registry_entry():
    """The extension point, pinned so it stays a one-liner."""
    assert set(skill.HARNESSES) == {"claude-code"}
    assert skill.HARNESSES["claude-code"].destination(Path("/p")) == Path(
        "/p/.claude/skills/inzaghi"
    )
