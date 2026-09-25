"""Which copy of Inzaghi is running.

There are no releases, so the package number is the same for every checkout
between two of them. What tells one running copy from another is the commit
under it, and only a source install has one to read.
"""

from __future__ import annotations

import subprocess

import pytest

from inzaghi import version


class Git:
    """Stands in for git, so no test depends on this repository's own state."""

    def __init__(self, **answers: str) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> str:
        self.calls.append(command)
        if "rev-parse" in command and "--abbrev-ref" in command:
            return self.answers.get("branch", "main")
        if "rev-parse" in command:
            return self.answers.get("commit", "1a2b3c4")
        if "status" in command:
            return self.answers.get("status", "")
        return ""  # pragma: no cover -- nothing else is asked


@pytest.fixture
def tree(tmp_path):
    """A directory that looks like a checkout, without being one."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def described(tree, **answers) -> str:
    return version.describe(tree, run=Git(**answers), checked=True)


# -- what it says ---------------------------------------------------------


def test_a_clean_checkout_names_its_branch_and_commit(tree):
    assert described(tree).endswith("· main 1a2b3c4")


def test_an_edited_tree_is_marked(tree):
    assert described(tree, status=" M src/inzaghi/ui/app.py").endswith("1a2b3c4*")


def test_an_untracked_file_counts_as_edited(tree):
    """`git status --porcelain` lists them and ignores what gitignore covers,
    which is the line between a scratch file and one that could be imported."""
    assert described(tree, status="?? src/inzaghi/experiment.py").endswith("*")


def test_a_detached_head_is_just_the_commit(tree):
    """There is no branch name to print, and the hash is the whole answer."""
    said = described(tree, branch="HEAD")
    assert said.endswith("· 1a2b3c4")
    assert "HEAD" not in said


def test_the_number_comes_first_either_way(tree):
    assert described(tree).startswith(version.installed_version())


def test_the_hash_is_asked_for_at_the_declared_length(tree):
    git = Git()
    version.describe(tree, run=git, checked=True)
    assert [f"--short={version.HASH_LENGTH}" in call for call in git.calls].count(True) == 1


# -- when there is nothing to read ----------------------------------------


def test_an_installed_wheel_says_only_its_version(tmp_path):
    """No working tree above it, which is the test -- not a marker we ship."""
    assert version.describe(tmp_path) == version.installed_version()


def test_a_git_that_will_not_answer_is_not_an_error(tree):
    """No git on PATH, or a tree on a mount that has stopped answering."""
    def broken(command):
        raise OSError("git: not found")

    assert version.describe(tree, run=broken, checked=True) == version.installed_version()


def test_a_git_that_times_out_is_not_an_error(tree):
    def slow(command):
        raise subprocess.TimeoutExpired(command, version.GIT_TIMEOUT)

    assert version.describe(tree, run=slow, checked=True) == version.installed_version()


def test_a_dot_git_that_holds_no_commit_yet(tree):
    """`git init` and nothing else: rev-parse fails and there is no hash."""
    assert version.describe(tree, run=Git(commit=""), checked=True) == version.installed_version()


# -- finding the tree -----------------------------------------------------


def test_the_tree_is_found_from_the_package(tmp_path):
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    (root / "src" / "inzaghi").mkdir(parents=True)
    assert version.source_root(root / "src" / "inzaghi" / "version.py") == root


def test_no_git_above_the_package_is_no_tree(tmp_path):
    here = tmp_path / "site-packages" / "inzaghi" / "version.py"
    here.parent.mkdir(parents=True)
    assert version.source_root(here) is None


def test_the_running_copy_answers_once(monkeypatch):
    """It shells out, and a commit made while the app is up is not the one
    the app is running."""
    version.line.cache_clear()
    calls = []
    monkeypatch.setattr(version, "describe", lambda: calls.append(1) or "x")
    assert version.line() == version.line() == "x"
    assert len(calls) == 1
    version.line.cache_clear()


def test_a_long_branch_is_cut_and_the_hash_survives(tree):
    """The hash identifies the build and comes last, so the branch gives way."""
    said = described(tree, branch="wip/" + "x" * 60)
    assert said.endswith("1a2b3c4")
    assert "…" in said
    assert len(said) < 60


def test_a_branch_at_the_limit_is_left_whole(tree):
    name = "x" * version.BRANCH_LENGTH
    assert f"{name} 1a2b3c4" in described(tree, branch=name)
