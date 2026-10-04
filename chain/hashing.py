from eth_utils import keccak


def keccak256(data: bytes) -> bytes:
    return keccak(data)


def to_hex(b: bytes) -> str:
    return "0x" + b.hex()


def from_hex(h: str) -> bytes:
    return bytes.fromhex(h[2:] if h.startswith("0x") else h)


def match_id_to_bytes32(match_id: str) -> bytes:
    """On-chain match id = keccak256(utf8(match_id))."""
    return keccak256(match_id.encode("utf-8"))


def encode_packed_commitment(role: int, seat: int, salt: bytes, match_id_b32: bytes) -> bytes:
    """Byte-for-byte abi.encodePacked(uint8 role, uint256 seat, bytes32 salt, bytes32 matchId)."""
    if not 0 <= role < 256:
        raise ValueError("role must fit in uint8")
    if seat < 0:
        raise ValueError("seat must be non-negative")
    if len(salt) != 32 or len(match_id_b32) != 32:
        raise ValueError("salt and match id must be 32 bytes")
    return role.to_bytes(1, "big") + seat.to_bytes(32, "big") + salt + match_id_b32


def role_commitment(role: int, seat: int, salt: bytes, match_id_b32: bytes) -> bytes:
    return keccak256(encode_packed_commitment(role, seat, salt, match_id_b32))


def event_log_hash(canonical_log_bytes: bytes) -> bytes:
    return keccak256(canonical_log_bytes)
