import threading

from abogi.channels.warm import MARGIN_SECONDS, WARM_UP, WarmConversation


class FakeConversation:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.asked: list[str] = []
        self._gate = gate

    def ask(self, text: str) -> str:
        if self._gate and text == WARM_UP:
            self._gate.wait(5)
        self.asked.append(text)
        return "ready" if text == WARM_UP else f"answer to {text}"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_first_warm_sends_warm_up_then_stays_warm():
    fake, clock = FakeConversation(), Clock()
    warm = WarmConversation(fake, idle_timeout=600, clock=clock)
    assert warm.is_cold()
    assert warm.warm() is True
    assert warm.warm() is False
    assert fake.asked == [WARM_UP]


def test_goes_cold_a_margin_before_the_idle_timeout():
    fake, clock = FakeConversation(), Clock()
    warm = WarmConversation(fake, idle_timeout=600, clock=clock)
    warm.ask("hello")
    clock.now += 600 - MARGIN_SECONDS - 1
    assert not warm.is_cold()
    clock.now += 2
    assert warm.is_cold()


def test_real_turns_keep_it_warm():
    fake, clock = FakeConversation(), Clock()
    warm = WarmConversation(fake, idle_timeout=600, clock=clock)
    warm.warm()
    clock.now += 500
    assert warm.ask("q1") == "answer to q1"
    clock.now += 500
    assert not warm.is_cold()


def test_ask_waits_for_a_background_warm_up_so_the_chain_stays_in_order():
    gate = threading.Event()
    fake = FakeConversation(gate)
    warm = WarmConversation(fake, idle_timeout=600, clock=Clock())
    thread = warm.warm_in_background()
    assert thread is not None
    asker = threading.Thread(target=warm.ask, args=("what is on tomorrow",))
    asker.start()
    gate.set()
    thread.join(5)
    asker.join(5)
    assert fake.asked == [WARM_UP, "what is on tomorrow"]


def test_background_warm_is_skipped_when_warm_and_swallows_errors():
    class Broken:
        def ask(self, text: str) -> str:
            raise RuntimeError("network")

    warm = WarmConversation(Broken(), idle_timeout=600, clock=Clock())
    thread = warm.warm_in_background()
    thread.join(5)
    assert warm.is_cold()  # failed warm-up leaves it cold, no exception escapes

    ok = WarmConversation(FakeConversation(), idle_timeout=600, clock=Clock())
    ok.warm()
    assert ok.warm_in_background() is None
