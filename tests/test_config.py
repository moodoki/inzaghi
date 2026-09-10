"""Reading the config file, including one written by a different version.

The same file is read by whatever Inzaghi is installed on each of a person's
machines, so a key one of them has never heard of is a fact of life rather
than an error.
"""

from __future__ import annotations

from inzaghi.config import Config


def test_an_alert_name_this_build_does_not_know_is_dropped(tmp_path):
    """The same config file is read by whatever versions are installed.

    ``[alerts]`` is applied wholesale, so a key added by a newer Inzaghi used
    to stop an older one from starting at all -- and ``reload`` catches only
    OSError and ValueError, so the TypeError was not even swallowed.
    """
    config = tmp_path / "inzaghi.toml"
    config.write_text(
        "[alerts]\nbell = false\ninvented_in_a_later_version = true\n", encoding="utf-8"
    )
    loaded = Config.load(config)
    assert loaded.alerts.bell is False  # the key it does know still applies
    assert not hasattr(loaded.alerts, "invented_in_a_later_version")


def test_a_config_being_edited_still_falls_back_to_the_running_one(tmp_path):
    config = tmp_path / "inzaghi.toml"
    config.write_text('[[channels]]\npath = "/x"\n', encoding="utf-8")
    loaded = Config.load(config)
    config.write_text("[[channels]\nbroken", encoding="utf-8")
    assert loaded.reload() is loaded
