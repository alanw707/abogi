"""What reaches Voice Live: no links or URLs, split at pauses."""

import pytest

from abogi.core.loop import PAUSE
from abogi.hosting.spoken import speakable, speakable_text


async def collect(chunks):
    async def source():
        for chunk in chunks:
            yield chunk

    return [chunk async for chunk in speakable(source())]


def test_citations_and_urls_are_not_read_aloud():
    text = "Python 3.15.2 is current (per python.org). ([python.org](https://www.python.org/downloads/latest/))"
    assert speakable_text(text) == "Python 3.15.2 is current (per python.org)."
    assert speakable_text("See [the docs](https://x.y/z) or https://a.b/c now.") == "See the docs or  now."


async def test_a_link_split_across_chunks_is_still_dropped():
    chunks = ["Python 3.15.2. ([py", "thon.org](https://www.py", "thon.org/))", " Done."]
    assert "".join(await collect(chunks)) == "Python 3.15.2. Done."


async def test_plain_text_streams_without_waiting():
    out = await collect(["Two ", "meetings ", "today."])
    assert len(out) == 3 and "".join(out) == "Two meetings today."


async def test_pauses_pass_through_and_flush_held_text():
    out = await collect(["Let me check (one", PAUSE, "Done."])
    after = out[out.index(PAUSE) + 1:]
    assert "".join(out[: out.index(PAUSE)]) == "Let me check (one"
    assert after == ["Done."]
