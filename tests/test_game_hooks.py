import time
import pytest
from game_hooks import GameHooksWorker


def test_game_hooks_worker():
    worker = GameHooksWorker(bridge_url="http://127.0.0.1:5000")

    # Test report_body local fallback / return
    ev = worker.report_body("RedPlayer")
    assert isinstance(ev, str)
    assert len(ev) > 0

    # Test queue methods (fire and forget, no exception thrown)
    worker.kill("RedPlayer", "BluePlayer", "Reactor")
    worker.vote("GreenPlayer", "RedPlayer")
    worker.eject("RedPlayer")
    worker.gameover(1, "Tasks complete")

    # Test token & round state compatibility
    worker.stake_tokens(10)
    assert worker.token_balance == 90
    worker.reward_tokens(25)
    assert worker.token_balance == 115

    worker.stop()
