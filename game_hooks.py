import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("clanfall.game_hooks")

DEFAULT_BRIDGE_URL = "http://127.0.0.1:5000"


class GameHooksWorker:
    """
    Fire-and-forget async HTTP queue worker.
    Pushes game events to the Python bridge service without blocking the main pygame thread.
    """

    def __init__(self, bridge_url: str = DEFAULT_BRIDGE_URL):
        self.bridge_url = bridge_url.rstrip("/")
        self._queue: queue.Queue = queue.Queue()
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()
        self.last_evidence: str = ""
        self.token_balance: int = 100
        self.round: int = 1
        self.deal_result: Optional[Dict[str, Any]] = None
        self._deal_started = False
        self._deal_lock = threading.Lock()

    def _post(self, path: str, data: Dict[str, Any], timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        url = f"{self.bridge_url}{path}"
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 201, 202):
                    res_body = resp.read().decode("utf-8")
                    return json.loads(res_body) if res_body else {}
        except Exception as exc:
            logger.debug("Bridge post to %s failed (fallback active): %s", path, exc)
        return None

    def _worker_loop(self):
        while self._running:
            try:
                item = self._queue.get(timeout=0.5)
                if item is None:
                    break
                path, data = item
                self._post(path, data)
                self._queue.task_done()
            except queue.Empty:
                continue

    def deal_roles(self, players: List[str], impostors: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        data = {"players": players, "impostors": impostors}
        return self._post("/deal", data, timeout=3.0)

    @staticmethod
    def _as_player_id(value: Any) -> Any:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    def report_kill(self, killer: str, victim: str, location: str = "Cafeteria"):
        self._queue.put(("/kill", {"killer": killer, "victim": victim, "location": location}))

    def report_body(self, reporter: str) -> str:
        """Synchronous query triggered when meeting starts. Returns surfaced memory evidence string."""
        res = self._post("/report", {"reporter": str(reporter)}, timeout=2.5)
        if res and "evidence" in res:
            self.last_evidence = res["evidence"]
            return res["evidence"]
        evidence = f"Surfaced Evidence: {reporter} reported a body. Movements detected near the area."
        self.last_evidence = evidence
        return evidence

    def report_vote(self, voter: str, target: Optional[str] = None):
        self._queue.put(("/vote", {"voter": str(voter), "target": str(target) if target else None}))

    def report_eject(self, ejected: Optional[str] = None):
        self._queue.put(("/eject", {"ejected": str(ejected) if ejected else None}))

    def report_game_over(self, winner: str = "CREW", reason: str = "Tasks completed"):
        self._queue.put(("/gameover", {"winner": str(winner), "reason": str(reason)}))

    def reset(self):
        return self._post("/reset", {}, timeout=2.0)

    # ------------------------------------------------------------------
    # Compatibility Aliases for game.py & server.py
    # ------------------------------------------------------------------
    def commit(self, players: List[Any]):
        p_names = [str(p) for p in players]
        self.deal_roles(p_names)

    def deal_async(self, players: List[Any], impostors: Optional[Any] = None, on_result=None):
        """
        Compatible with Among-Midnight: deal_async(player_ids, callback).
        Also accepts deal_async(players, impostors=[...]).
        """
        if callable(impostors) and on_result is None:
            on_result = impostors
            impostors = None

        with self._deal_lock:
            if self._deal_started:
                return
            self._deal_started = True

        p_names = [str(p) for p in players]
        imp_names = [str(i) for i in impostors] if impostors else None

        def _run():
            result = self.deal_roles(p_names, imp_names)
            if result is None:
                saboteur = self._as_player_id(p_names[0]) if p_names else None
                result = {"saboteurPlayerId": saboteur, "fallback": True}
            elif "saboteurPlayerId" in result:
                result = dict(result)
                result["saboteurPlayerId"] = self._as_player_id(result["saboteurPlayerId"])
            self.deal_result = result
            if on_result:
                on_result(result)

        threading.Thread(target=_run, daemon=True, name="clanfall-deal").start()

    def kill(self, killer: Any, victim: Any, location: str = "Cafeteria"):
        self.report_kill(str(killer), str(victim), location)

    def vote(self, voter: Any, target: Any):
        self.report_vote(str(voter), str(target) if target else None)

    def eject(self, ejected: Any = None):
        self.report_eject(str(ejected) if ejected else None)

    def gameover(self, winner: Any = 1, reason: str = "end"):
        w_str = "CREW" if winner in (1, "1", "CREW") else "IMPOSTOR"
        self.report_game_over(w_str, str(reason))

    def stake_tokens(self, amount: int = 10):
        self.token_balance = max(0, self.token_balance - amount)

    def reward_tokens(self, amount: int = 25):
        self.token_balance += amount

    def get_token_balance(self) -> int:
        return self.token_balance

    def stop(self):
        self._running = False
        self._queue.put(None)


PLAYERS_PER_MATCH = 6
hooks = GameHooksWorker()
