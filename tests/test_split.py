"""The divider between the timeline and the reader: dragged, keyed, shared."""

from __future__ import annotations


from inzaghi.ui import split
from inzaghi.ui.channel_view import ChannelPane
from test_ui import make_app, open_channel, settle


def left_width(app) -> int:
    return app.query_one(ChannelPane).query_one("#left").size.width


async def test_ctrl_w_angle_brackets_move_the_divider_and_equals_puts_it_back(channel_root):
    app = make_app(channel_root)
    async with app.run_test(size=(200, 50)) as pilot:
        await open_channel(app, pilot)
        start = left_width(app)

        await pilot.press("ctrl+w", "greater_than_sign")
        await pilot.pause()
        assert app.split_percent == split.START_PERCENT + split.STEP_PERCENT
        assert left_width(app) > start

        await pilot.press("ctrl+w", "less_than_sign", "ctrl+w", "less_than_sign")
        await pilot.pause()
        assert app.split_percent == split.START_PERCENT - split.STEP_PERCENT
        assert left_width(app) < start

        await pilot.press("ctrl+w", "equals_sign")
        await pilot.pause()
        assert app.split_percent == split.START_PERCENT
        assert left_width(app) == start


async def test_the_keys_mean_nothing_without_the_prefix(channel_root):
    """``<``, ``>`` and ``=`` are priority bindings, refused unless armed."""
    app = make_app(channel_root)
    async with app.run_test(size=(200, 50)) as pilot:
        await open_channel(app, pilot)
        await pilot.press("greater_than_sign", "less_than_sign", "equals_sign")
        await pilot.pause()
        assert app.split_percent == split.START_PERCENT


async def test_neither_column_can_be_closed(channel_root):
    app = make_app(channel_root)
    async with app.run_test(size=(200, 50)) as pilot:
        await open_channel(app, pilot)
        for _ in range(30):
            await pilot.press("ctrl+w", "greater_than_sign")
        await pilot.pause()
        assert app.split_percent == split.MAX_PERCENT
        for _ in range(30):
            await pilot.press("ctrl+w", "less_than_sign")
        await pilot.pause()
        assert app.split_percent == split.MIN_PERCENT
        assert app.query_one(ChannelPane).query_one("#reader-column").size.width > 0


async def test_dragging_the_divider_moves_it(channel_root):
    """Columns dragged become a share of the width: 20 of 200 is ten points.

    Posted as the divider's own message, the way the preview's grip is
    tested: the pilot's simulated pointer carries no movement deltas.
    """
    app = make_app(channel_root)
    async with app.run_test(size=(200, 50)) as pilot:
        pane = await open_channel(app, pilot)
        start = left_width(app)
        pane.query_one("#split").post_message(split.Divider.Dragged(20))
        await pilot.pause()
        assert app.split_percent == split.START_PERCENT + 10
        assert left_width(app) > start
        pane.query_one("#split").post_message(split.Divider.Dragged(-500))  # thrown left
        await pilot.pause()
        assert app.split_percent == split.MIN_PERCENT


async def test_the_split_is_one_for_every_channel(tmp_path, channel_root):
    """Changing tab must not move the divider."""
    from inzaghi.config import ChannelSpec, Config
    from inzaghi.ui.app import InzaghiApp
    import subprocess

    other = tmp_path / "other"
    subprocess.run(["cp", "-Rp", str(channel_root), str(other)], check=True)
    config = Config(
        channels=[ChannelSpec(path=channel_root, name="one"), ChannelSpec(path=other, name="two")]
    )
    app = InzaghiApp(config)
    async with app.run_test(size=(200, 50)) as pilot:
        await settle(app, pilot)
        await open_channel(app, pilot)
        await pilot.press("ctrl+w", "greater_than_sign")
        await pilot.pause()
        panes = list(app.query(ChannelPane))
        assert len(panes) == 2
        widths = {p.query_one("#left").styles.width.value for p in panes}
        assert widths == {split.START_PERCENT + split.STEP_PERCENT}
