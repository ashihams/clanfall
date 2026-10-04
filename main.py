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

    print(f"[Clanfall] API on http://{config.api_host}:{config.api_port}  (dashboard: /dashboard/, docs: /docs)")
    uvicorn.run("api.match_api:app", host=config.api_host, port=config.api_port, reload=False)


def main():
    parser = argparse.ArgumentParser(description="Clanfall match-memory engine")
    parser.add_argument("--demo", action="store_true", help="Run the one-command CLI demo")
    parser.add_argument("--api", action="store_true", help="Run the FastAPI service + dashboard")
    args = parser.parse_args()
    if args.api:
        run_api()
    else:
        run_demo()


if __name__ == "__main__":
    main()
