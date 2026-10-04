import os
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

CONFIG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CONFIG_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")

CONFIG_PATH = CONFIG_DIR / "config.yaml"


def _env(key: str, default: Any = None) -> Any:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    return val


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


class HybridWeightsConfig(BaseModel):
    vector_similarity: float = 0.6
    graph_relevance: float = 0.4


class TencentVectorDBConfig(BaseModel):
    url: str = ""
    username: str = "root"
    key: str = ""
    database: str = "clanfall"
    collection: str = "match_facts"
    timeout_s: int = 10


class PineconeConfig(BaseModel):
    enabled: bool = True
    api_key: str = ""
    index: str = "clanfall-match-facts"
    cloud: str = "aws"
    region: str = "us-east-1"
    # Each engine instance writes to its own "<prefix>-<id>" namespace and deletes only that namespace
    # on wipe, so a shared index (and any other app's vectors in it) is never touched.
    namespace_prefix: str = "clanfall"
    # Serverless Pinecone is eventually consistent; wait this long for fresh writes to become queryable.
    freshness_wait_s: float = 3.0


class Neo4jConfig(BaseModel):
    enabled: bool = True
    uri: str = ""
    user: str = "neo4j"
    password: str = ""
    database: str = "neo4j"
    # "bolt" (port 7687), "http" (Query API over HTTPS, for networks that block 7687), or "auto" = bolt then http.
    transport: str = "auto"
    # Query API base URL override; derived from the URI host when empty (https://<host>).
    http_url: str = ""
    timeout_s: float = 8.0
    # Kept short so "auto" falls back to HTTPS quickly on networks that silently drop 7687.
    bolt_connect_timeout_s: float = 3.0
    # Every node carries the ClanfallNode label and an "engine" property "<prefix>-<id>"; a wipe deletes
    # exactly those nodes, so a shared database (e.g. AeroCortex's graph) is never touched.
    scope_prefix: str = "clanfall"


class MongoConfig(BaseModel):
    enabled: bool = True
    uri: str = ""
    database: str = "clanfall"
    timeout_ms: int = 8000
    # Documents carry an "engine" field "<prefix>-<id>"; a wipe deletes exactly those documents.
    scope_prefix: str = "clanfall"


class MemoryConfig(BaseModel):
    hybrid_weights: HybridWeightsConfig = Field(default_factory=HybridWeightsConfig)
    top_k_facts: int = 5
    embedding_dim: int = 64
    # "auto" tries tencent -> pinecone -> chroma -> in-memory; or force one of them.
    vector_backend: str = "auto"
    chroma_collection: str = "clanfall_match_facts"
    tencent: TencentVectorDBConfig = Field(default_factory=TencentVectorDBConfig)
    pinecone: PineconeConfig = Field(default_factory=PineconeConfig)
    # "auto" uses Neo4j when configured and reachable, else NetworkX; "networkx" forces the embedded graph.
    graph_backend: str = "auto"
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    mongo: MongoConfig = Field(default_factory=MongoConfig)


class RulesConfig(BaseModel):
    near_body_window_s: float = 20.0
    last_seen_window_s: float = 30.0


class ChainConfig(BaseModel):
    # "mock" replicates MatchLedger.sol in-process; "testnet" sends real transactions.
    mode: str = "mock"
    rpc_url: str = ""
    private_key: str = ""
    ledger_address: str = ""
    chain_id: int = 84532
    explorer_tx_url: str = "https://sepolia.basescan.org/tx/"
    # Stretch: ERC-721 Match Passport minted at match end, and the opt-in leaderboard.
    nft_enabled: bool = True
    passport_nft_address: str = ""
    leaderboard_address: str = ""
    # Sent from the host wallet to a player's throwaway wallet so the player can pay for their own opt-in tx.
    leaderboard_gas_topup_eth: float = 0.00002


class LLMConfig(BaseModel):
    groq_enabled: bool = True
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = 8.0
    groq_connect_timeout_seconds: float = 2.0


class SystemConfig(BaseModel):
    app_name: str = "Clanfall Match-Memory Engine"
    version: str = "0.1.0"
    log_level: str = "INFO"


class AppConfig(BaseModel):
    system: SystemConfig = Field(default_factory=SystemConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    rules: RulesConfig = Field(default_factory=RulesConfig)
    chain: ChainConfig = Field(default_factory=ChainConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_key: str = ""
    passport_dir: str = "data/passports"


def _overlay_env(cfg: AppConfig) -> AppConfig:
    """Environment variables win over config.yaml defaults."""
    cfg.api_host = _env("API_HOST", cfg.api_host)
    cfg.api_port = _env_int("API_PORT", _env_int("PORT", cfg.api_port))
    cfg.api_key = _env("API_KEY", cfg.api_key)
    cfg.passport_dir = _env("PASSPORT_DIR", cfg.passport_dir)

    cfg.memory.vector_backend = _env("VECTOR_BACKEND", cfg.memory.vector_backend).strip().lower()
    tc = cfg.memory.tencent
    tc.url = _env("TENCENT_VDB_URL", tc.url)
    tc.username = _env("TENCENT_VDB_USERNAME", tc.username)
    tc.key = _env("TENCENT_VDB_KEY", tc.key)
    tc.database = _env("TENCENT_VDB_DATABASE", tc.database)
    tc.collection = _env("TENCENT_VDB_COLLECTION", tc.collection)
    pc = cfg.memory.pinecone
    pc.enabled = _env_bool("PINECONE_ENABLED", pc.enabled)
    pc.api_key = _env("PINECONE_API_KEY", pc.api_key)
    pc.index = _env("PINECONE_INDEX", pc.index)
    pc.cloud = _env("PINECONE_CLOUD", pc.cloud)
    pc.region = _env("PINECONE_REGION", pc.region)
    pc.namespace_prefix = _env("PINECONE_NAMESPACE_PREFIX", pc.namespace_prefix)

    cfg.memory.graph_backend = _env("GRAPH_BACKEND", cfg.memory.graph_backend).strip().lower()
    nj = cfg.memory.neo4j
    nj.enabled = _env_bool("NEO4J_ENABLED", nj.enabled)
    nj.uri = _env("NEO4J_URI", nj.uri)
    nj.user = _env("NEO4J_USER", _env("NEO4J_USERNAME", nj.user))
    nj.password = _env("NEO4J_PASSWORD", nj.password)
    nj.database = _env("NEO4J_DATABASE", nj.database)
    nj.transport = _env("NEO4J_TRANSPORT", nj.transport).strip().lower()
    nj.http_url = _env("NEO4J_HTTP_URL", nj.http_url)
    nj.scope_prefix = _env("NEO4J_SCOPE_PREFIX", nj.scope_prefix)
    mg = cfg.memory.mongo
    mg.enabled = _env_bool("MONGO_ENABLED", mg.enabled)
    mg.uri = _env("MONGO_URI", mg.uri)
    mg.database = _env("MONGO_DB", mg.database)
    mg.timeout_ms = _env_int("MONGO_TIMEOUT_MS", mg.timeout_ms)
    mg.scope_prefix = _env("MONGO_SCOPE_PREFIX", mg.scope_prefix)

    cfg.chain.mode = _env("CHAIN_MODE", cfg.chain.mode).strip().lower()
    cfg.chain.rpc_url = _env("CHAIN_RPC_URL", cfg.chain.rpc_url)
    cfg.chain.private_key = _env("CHAIN_PRIVATE_KEY", cfg.chain.private_key)
    cfg.chain.ledger_address = _env("MATCH_LEDGER_ADDRESS", cfg.chain.ledger_address)
    cfg.chain.chain_id = _env_int("CHAIN_ID", cfg.chain.chain_id)
    cfg.chain.explorer_tx_url = _env("CHAIN_EXPLORER_TX_URL", cfg.chain.explorer_tx_url)
    cfg.chain.nft_enabled = _env_bool("NFT_ENABLED", cfg.chain.nft_enabled)
    cfg.chain.passport_nft_address = _env("PASSPORT_NFT_ADDRESS", cfg.chain.passport_nft_address)
    cfg.chain.leaderboard_address = _env("LEADERBOARD_ADDRESS", cfg.chain.leaderboard_address)
    try:
        cfg.chain.leaderboard_gas_topup_eth = float(_env("LEADERBOARD_GAS_TOPUP_ETH", cfg.chain.leaderboard_gas_topup_eth))
    except (TypeError, ValueError):
        pass

    cfg.llm.groq_enabled = _env_bool("GROQ_ENABLED", cfg.llm.groq_enabled)
    cfg.llm.groq_api_key = _env("GROQ_API_KEY", cfg.llm.groq_api_key)
    cfg.llm.groq_model = _env("GROQ_MODEL", cfg.llm.groq_model)
    cfg.llm.groq_base_url = _env("GROQ_BASE_URL", cfg.llm.groq_base_url)
    return cfg


def load_config(config_file: Optional[Path] = None) -> AppConfig:
    target_file = config_file or CONFIG_PATH
    data: dict = {}
    if target_file.exists():
        with open(target_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    api_data = data.get("api", {})
    cfg = AppConfig(
        system=SystemConfig(**data.get("system", {})),
        memory=MemoryConfig(**data.get("memory", {})),
        rules=RulesConfig(**data.get("rules", {})),
        chain=ChainConfig(**data.get("chain", {})),
        llm=LLMConfig(**data.get("llm", {})),
        api_host=api_data.get("host", "127.0.0.1"),
        api_port=api_data.get("port", 8000),
        passport_dir=data.get("passport_dir", "data/passports"),
    )
    return _overlay_env(cfg)


config = load_config()
