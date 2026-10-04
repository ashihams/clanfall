import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import config


def run_demo():
    from demo import main as demo_main
    demo_main([])


def run_api():
    import uvicorn
    print(f"[Clanfall] API on http://{config.api_host}:{config.api_port} (dashboard: /dashboard/, docs: /docs)")
    uvicorn.run("api.match_api:app", host=config.api_host, port=config.api_port, reload=False)


def run_bridge(port: int = 5000):
    import uvicorn
    print(f"[Clanfall] Python Bridge on http://127.0.0.1:{port} (audit dashboard: /audit)")
    uvicorn.run("bridge.server:app", host="127.0.0.1", port=port, reload=False)


def run_server():
    print("[Clanfall] Starting 2D LAN Relay Server on port 4321...")
    import server
    server.run_server()


def run_client():
    print("[Clanfall] Launching 2D Pygame Game Client...")
    import game
    while True:
        g = game.Game()
        g.menu.game_intro()
        del g


def main():
    parser = argparse.ArgumentParser(description="Clanfall match-memory engine & 2D game launcher")
    parser.add_argument("--demo", action="store_true", help="Run the one-command CLI demo")
    parser.add_argument("--api", action="store_true", help="Run the main FastAPI service + dashboard")
    parser.add_argument("--bridge", action="store_true", help="Run the Python Bridge service (default port 5000)")
    parser.add_argument("--port", type=int, default=5000, help="Port for bridge service")
    parser.add_argument("--server", action="store_true", help="Run the 2D LAN relay server")
    parser.add_argument("--client", action="store_true", help="Run the 2D Pygame game client")
    args = parser.parse_args()

    if args.bridge:
        run_bridge(args.port)
    elif args.server:
        run_server()
    elif args.client:
        run_client()
    elif args.api:
        run_api()
    else:
        run_demo()


if __name__ == "__main__":
    main()
