import secrets
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from chain.hashing import match_id_to_bytes32, role_commitment, to_hex
from models import Role


@dataclass(frozen=True)
class SeatAssignment:
    seat: int
    player: str
    role: Role
    salt: bytes
    commitment: bytes


class RoleBook:
    """
    Commit-reveal role assignment for one match.

    commitment[seat] = keccak256(abi.encodePacked(uint8 role, uint256 seat, bytes32 salt, bytes32 matchId))

    Salts stay inside this object until reveal(); only commitments are public
    while the match is running.
    """

    def __init__(self, match_id: str, players: Sequence[str], impostors: Sequence[str]):
        unknown = set(impostors) - set(players)
        if unknown:
            raise ValueError(f"impostors not in player list: {sorted(unknown)}")
        if not impostors:
            raise ValueError("at least one impostor is required")
        self.match_id = match_id
        self.match_id_b32 = match_id_to_bytes32(match_id)
        self._seats: List[SeatAssignment] = []
        for seat, player in enumerate(players):
            role = Role.IMPOSTOR if player in impostors else Role.CREW
            salt = secrets.token_bytes(32)
            self._seats.append(SeatAssignment(
                seat=seat,
                player=player,
                role=role,
                salt=salt,
                commitment=role_commitment(int(role), seat, salt, self.match_id_b32),
            ))
        self.revealed = False

    @property
    def commitments(self) -> List[bytes]:
        return [s.commitment for s in self._seats]

    def public_view(self) -> List[Dict[str, str]]:
        return [{"seat": s.seat, "player": s.player, "commitment": to_hex(s.commitment)} for s in self._seats]

    def roles(self) -> Dict[str, Role]:
        """Ground truth for the simulation engine (server-side only)."""
        return {s.player: s.role for s in self._seats}

    def seat_of(self, player: str) -> int:
        return next(s.seat for s in self._seats if s.player == player)

    def reveal(self) -> Tuple[List[int], List[int], List[bytes]]:
        """Arguments for MatchLedger.revealAndFinalize: (roles, seats, salts)."""
        self.revealed = True
        return (
            [int(s.role) for s in self._seats],
            [s.seat for s in self._seats],
            [s.salt for s in self._seats],
        )

    def assignments(self) -> List[SeatAssignment]:
        if not self.revealed:
            raise PermissionError("roles are sealed until reveal()")
        return list(self._seats)


def verify_commitment(match_id: str, role: int, seat: int, salt: bytes, commitment: bytes) -> bool:
    """Anyone can recompute a commitment from the revealed (role, seat, salt)."""
    return role_commitment(role, seat, salt, match_id_to_bytes32(match_id)) == commitment
