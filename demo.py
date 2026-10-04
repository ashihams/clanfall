"""One-command Clanfall demo (follows the demo script in the build spec).

    python demo.py              # match 1 (crew catches a liar) + match 2 (impostor gets away), wipe in between
    python demo.py --only reactor_lie
"""
import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestration.match_runner import MatchOrchestrator, MatchOutcome
from simulation.scenario import get_scenario

WIDTH = 88


def banner(text: str) -> None:
    print("\n" + "=" * WIDTH)
    for line in text.splitlines():
        print(f" {line}")
    print("=" * WIDTH)


def section(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, WIDTH - len(title) - 5))


def show_stats(label: str, stats: Dict[str, Any]) -> None:
    keys = ["episodic_events", "semantic_facts", "vector_documents", "graph_nodes", "graph_edges", "evidence_items"]
    print(f"  {label}: " + "  ".join(f"{k}={stats[k]}" for k in keys))


def describe(event: Dict[str, Any]) -> Optional[str]:
    a, actor, loc, tgt = event["action"], event.get("actor"), event.get("location"), event.get("target")
    if a == "match_start":
        return f"{', '.join(event['detail']['players'])} spawn in {loc}"
    if a == "move":
        return f"{actor} walks {event['detail'].get('from')} -> {loc}"
    if a == "task":
        return f"{actor} completes a task in {loc}"
    if a == "kill":
        return "[sealed] a hidden event is written to the episodic log (memory cannot read it)"
    if a == "report":
        return f"{actor} REPORTS {tgt}'s body in {loc}"
    if a == "meeting_start":
        return f"EMERGENCY MEETING #{event['meeting_id']}"
    if a == "claim":
        return f'{actor}: "I was in {loc} at {event["claimed_at"]:.0f}s."'
    if a == "voting_open":
        return "Voting opens"
    if a == "vote":
        return f"{actor} votes {'skip' if tgt is None else tgt}"
    if a == "eject":
        return f"{tgt or 'Nobody'} was ejected   (tally {event['detail'].get('tally')})"
    if a == "game_over":
        return f"GAME OVER: {event['detail']['result']} ({event['detail']['reason']})"
    return None


def evidence_card(ev: Dict[str, Any]) -> None:
    print("    +" + "-" * (WIDTH - 6) + "+")
    print(f"    | MEETING EVIDENCE ({ev['stage']}) — surfaced by hybrid memory retrieval")
    print(f"    | {ev['headline']}")
    print(
        f"    | score {ev['final_score']:.2f} = 0.6 x vector {ev['vector_similarity']:.2f} "
        f"+ 0.4 x graph {ev['graph_relevance']:.2f}   [source: {ev['source']}]"
    )
    for s in ev.get("supporting", []):
        print(f"    |   also: {s}")
    print("    +" + "-" * (WIDTH - 6) + "+")


def play(orch: MatchOrchestrator, key: str, verbose: bool = True) -> MatchOutcome:
    scenario = get_scenario(key)
    banner(f"MATCH: {scenario.title}\n{scenario.description}")

    section("1. Fresh match - memory is empty, nobody knows what happened before")
    show_stats("memory before start", orch.memory.stats())
    session = orch.start_match(key)
    print(f"  match_id {session.match_id}")
    print(f"  roles committed BEFORE play via {session.ledger.mode} ledger, tx {session.commit_receipt.tx_hash}")
    if session.commit_receipt.explorer_url:
        print(f"  {session.commit_receipt.explorer_url}")
    for c in session.role_book.public_view():
        print(f"    seat {c['seat']} {c['player']:<7} commitment {c['commitment']}")

    section("2. Match plays out (only public events reach semantic memory)")
    while not session.engine.done:
        for rec in orch.step():
            ev = rec["event"]
            line = describe(ev)
            if line and (verbose or ev["action"] not in ("move", "task", "vote")):
                print(f"  t={ev['timestamp']:>4.0f}s  {line}")
            for log in rec.get("logs", []):
                if "[SEMANTIC]" in log:
                    flag = "!! LIVE CATCH !! " if "ALIBI_CONTRADICTION" in log else ""
                    print(f"           {flag}memory derived -> {log.split('] ', 1)[1]}")
            if rec.get("evidence"):
                evidence_card(rec["evidence"])
    show_stats("memory at match end", orch.memory.stats())

    outcome = orch.end_match()
    p = outcome.passport

    section("3. Match Passport")
    print(f"  result     {p.result}  ({p.reason})")
    print(f"  duration   {p.duration_s:.0f}s | tasks {p.task_count} | meetings {p.meeting_count} | "
          f"kills {p.kill_count} | ejected {p.ejections or '-'} | events {p.event_count}")
    print(f"  sigil      {p.sigil.get('title')} -> {p.sigil.get('image')}")
    print(f"  signed by host {p.host_address}  signature valid: {p.verify_signature()}")

    section("4. Commit-reveal: were the roles fixed before the match?")
    for s in p.players:
        print(f"  seat {s.seat} {s.player:<7} {s.role:<9} commitment matches: "
              f"local={s.verified_locally} ledger={s.verified_on_ledger}")
    print(f"  event log hash  {p.event_log_hash}")
    print(f"  on-ledger hash  {p.ledger.on_chain_event_log_hash}  (phase: {p.ledger.final_phase})")
    print(f"  reveal tx       {p.ledger.reveal_tx}  [{p.ledger.mode}]")
    if p.ledger.reveal_explorer_url:
        print(f"  {p.ledger.reveal_explorer_url}")
    if p.nft:
        print(f"  Match Passport NFT  token #{p.nft.token_id} on {p.nft.contract} [{p.nft.mode}]")
        print(f"    minted to {p.nft.owner}, tx {p.nft.mint_tx}")
        if p.nft.token_url:
            print(f"    {p.nft.token_url}")
    elif p.nft_error:
        print(f"  Match Passport NFT  not minted ({p.nft_error})")
    if outcome.export_dir:
        print(f"  verifiable record written to {outcome.export_dir}")
        print(f"  verify it yourself: python -m chain.verify \"{outcome.export_dir}\"")

    section("5. Memory wipe (enforced, not claimed)")
    show_stats("before wipe", outcome.wipe["before"])
    show_stats("after wipe ", outcome.wipe["after"])
    print(f"  wiped: {outcome.wipe['wiped']}")
    return outcome


def prove_amnesia(orch: MatchOrchestrator, previous: MatchOutcome) -> None:
    section("Between matches: does anything from the last match survive?")
    probe = "Blue claimed Reactor but was in Electrical near Green's body"
    ctx = orch.graph.memory_agent.retrieve(probe, top_k=5)
    print(f'  hybrid query: "{probe}"')
    print(f"  results: {len(ctx.retrieved_facts)}  (previous match {previous.passport.match_id[:8]}... is gone)")


def opt_in(orch: MatchOrchestrator, players: List[str]) -> None:
    section("Optional: players choose to publish their result (separate from match memory)")
    print("  Match memory is already wiped. The leaderboard is a separate, opt-in record:")
    print("  only players who ask are published, from their own wallet, with a host-signed result.")
    for name in players:
        try:
            e = orch.opt_in_leaderboard(name)
        except Exception as exc:
            print(f"  {name}: not published ({exc})")
            continue
        r = e["record"]
        print(f"  {name:<7} {'WON ' if e['won'] else 'LOST'} as {e['role']:<9} -> profile {e['profile_address']} "
              f"now {r['wins']}W/{r['losses']}L  [{e['mode']}]")
        if e.get("explorer_url"):
            print(f"          {e['explorer_url']}")


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Clanfall demo")
    parser.add_argument("--only", choices=["reactor_lie", "clean_getaway"], help="run a single scenario")
    parser.add_argument("--quiet", action="store_true", help="hide moves, tasks, and individual votes")
    parser.add_argument("--export-dir", default=None, help="where to write passports (default: data/passports)")
    parser.add_argument("--opt-in", nargs="+", default=[], metavar="PLAYER",
                        help="after each match, these players choose to publish their result to the leaderboard")
    args = parser.parse_args(argv)

    banner(
        "CLANFALL - match-memory engine for multiplayer games\n"
        "episodic -> semantic -> knowledge graph, hybrid retrieval (0.6 vector + 0.4 graph),\n"
        "commit-reveal roles + on-ledger event-log hash, full memory wipe every match.\n"
        "Memory architecture forked from AeroCortex (a UAV failure-recovery system)."
    )
    orch = MatchOrchestrator(export_dir=args.export_dir)
    print(f"\n  vector backend: {orch.memory.vector_store.engine} | graph: {orch.memory.graph.engine} "
          f"| ledger: {orch.ledger.mode} | passport NFT: {orch.nft.mode if orch.nft else 'off'} "
          f"| leaderboard: {orch.leaderboard.mode}")

    keys = [args.only] if args.only else ["reactor_lie", "clean_getaway"]
    previous = None
    for key in keys:
        if previous is not None:
            prove_amnesia(orch, previous)
        previous = play(orch, key, verbose=not args.quiet)
        if args.opt_in:
            opt_in(orch, args.opt_in)

    if args.opt_in:
        section("Leaderboard (opt-in only)")
        for row in orch.leaderboard_standings():
            print(f"  {row['player'] or row['address']:<10} {row['wins']}W / {row['losses']}L   {row['address']}")
    else:
        print("\n  (optional) let players publish their results: python demo.py --opt-in Red Blue")

    banner(
        "A pluggable memory engine, shown here on a tiny social-deduction prototype.\n"
        "Any multiplayer game can POST /event and get in-match memory, live evidence,\n"
        "and a verifiable match record - and it forgets everything when the match ends."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
