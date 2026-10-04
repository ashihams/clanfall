"""Compile the Clanfall contracts to chain/contracts/build/<Name>.json (abi + bytecode).

    python -m chain.contracts.compile

MatchPassport and ClanfallLeaderboard import OpenZeppelin v5.0.2, which is
cloned into chain/contracts/lib/ on first compile.
"""
import json
import subprocess
from pathlib import Path

SOLC_VERSION = "0.8.24"
OZ_VERSION = "v5.0.2"
HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
OZ_DIR = HERE / "lib" / "openzeppelin-contracts"
CONTRACTS = ["MatchLedger", "MatchPassport", "ClanfallLeaderboard"]


def ensure_openzeppelin() -> Path:
    if not (OZ_DIR / "contracts").exists():
        OZ_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", OZ_VERSION,
             "https://github.com/OpenZeppelin/openzeppelin-contracts.git", str(OZ_DIR)],
            check=True,
        )
    return OZ_DIR


def compile_contract(name: str = "MatchLedger") -> Path:
    return compile_all([name])[0]


def compile_all(names=None) -> list:
    import solcx

    names = names or CONTRACTS
    if SOLC_VERSION not in [str(v) for v in solcx.get_installed_solc_versions()]:
        solcx.install_solc(SOLC_VERSION)
    oz = ensure_openzeppelin()
    out = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {f"{n}.sol": {"content": (HERE / f"{n}.sol").read_text(encoding="utf-8")} for n in names},
            "settings": {
                "remappings": [f"@openzeppelin/contracts/={(oz / 'contracts').as_posix()}/"],
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
            },
        },
        solc_version=SOLC_VERSION,
        allow_paths=[str(HERE), str(oz)],
    )
    BUILD.mkdir(exist_ok=True)
    written = []
    for n in names:
        contract = out["contracts"][f"{n}.sol"][n]
        target = BUILD / f"{n}.json"
        target.write_text(json.dumps({
            "contractName": n,
            "compiler": f"solc-{SOLC_VERSION}",
            "abi": contract["abi"],
            "bytecode": "0x" + contract["evm"]["bytecode"]["object"],
        }, indent=2), encoding="utf-8")
        written.append(target)
    return written


if __name__ == "__main__":
    for path in compile_all():
        print(f"wrote {path}")
