"""Deploy the Clanfall contracts to the configured EVM testnet (default: Base Sepolia).

    set CHAIN_RPC_URL=https://sepolia.base.org
    set CHAIN_PRIVATE_KEY=0x...        # a funded *testnet-only* key
    python -m chain.contracts.deploy           # deploys whatever is not configured yet
    python -m chain.contracts.deploy --force   # redeploy all three

MatchLedger is reused if MATCH_LEDGER_ADDRESS is set. MatchPassport (ERC-721) and
ClanfallLeaderboard are bound to that ledger; the deployer becomes the NFT owner and
the leaderboard's result attestor. Paste the printed lines into .env.
"""
import argparse
import json
import sys
from pathlib import Path

from chain.ledger import BUILD_DIR, Web3Ledger
from config import config

DEPLOYMENT_FILE = Path(__file__).resolve().parent / "deployment.json"
ARTIFACTS = ["MatchLedger", "MatchPassport", "ClanfallLeaderboard"]


def main(argv=None) -> int:
    from web3 import Web3

    from chain.leaderboard import Web3Leaderboard
    from chain.nft import Web3PassportNFT

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="redeploy every contract")
    args = parser.parse_args(argv)

    c = config.chain
    if not (c.rpc_url and c.private_key):
        print("CHAIN_RPC_URL and CHAIN_PRIVATE_KEY must be set (see .env.example)")
        return 1
    if not all((BUILD_DIR / f"{name}.json").exists() for name in ARTIFACTS):
        from chain.contracts.compile import compile_all

        compile_all()

    w3 = Web3(Web3.HTTPProvider(c.rpc_url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print(f"cannot reach {c.rpc_url}")
        return 1
    deployer = w3.eth.account.from_key(c.private_key).address
    balance = w3.eth.get_balance(deployer)
    print(f"chain id {w3.eth.chain_id} | deployer {deployer} | balance {w3.from_wei(balance, 'ether')} ETH")
    if balance == 0:
        print("deployer has no testnet funds - use a faucet first")
        return 1

    if c.ledger_address and not args.force:
        ledger = Web3Ledger(w3, c.ledger_address, private_key=c.private_key, explorer_tx_url=c.explorer_tx_url)
        print(f"MatchLedger          reusing   {ledger.address}")
    else:
        ledger = Web3Ledger.deploy(w3, private_key=c.private_key, explorer_tx_url=c.explorer_tx_url)
        print(f"MatchLedger          deployed  {ledger.address}")

    if c.passport_nft_address and not args.force:
        nft_address = w3.to_checksum_address(c.passport_nft_address)
        print(f"MatchPassport        reusing   {nft_address}")
    else:
        nft_address = Web3PassportNFT.deploy(ledger).address
        print(f"MatchPassport        deployed  {nft_address}")

    if c.leaderboard_address and not args.force:
        board_address = w3.to_checksum_address(c.leaderboard_address)
        print(f"ClanfallLeaderboard  reusing   {board_address}")
    else:
        board_address = Web3Leaderboard.deploy(ledger).address
        print(f"ClanfallLeaderboard  deployed  {board_address}")

    DEPLOYMENT_FILE.write_text(json.dumps({
        "chain_id": w3.eth.chain_id,
        "deployer": deployer,
        "MatchLedger": ledger.address,
        "MatchPassport": nft_address,
        "ClanfallLeaderboard": board_address,
    }, indent=2), encoding="utf-8")
    print("\nadd to .env:")
    print(f"  CHAIN_MODE=testnet")
    print(f"  MATCH_LEDGER_ADDRESS={ledger.address}")
    print(f"  PASSPORT_NFT_ADDRESS={nft_address}")
    print(f"  LEADERBOARD_ADDRESS={board_address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
