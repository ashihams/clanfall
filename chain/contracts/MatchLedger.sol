// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title MatchLedger
/// @notice Provably fair role assignment (commit-reveal) + tamper-evident
///         match history commitment for a single social-deduction match.
///
/// Design notes (hackathon scope — intentionally minimal):
///  - No ZK. We only need tamper-evidence, not privacy, so a plain hash
///    commitment is sufficient (see build spec §8.1 for rationale).
///  - One contract instance is reused across matches via `matchId`, so you
///    do not need to redeploy between simulated matches.
///  - Gas/complexity kept low on purpose: this is a hackathon demo, not a
///    production game backend.

contract MatchLedger {
    // ---------------------------------------------------------------
    // Types
    // ---------------------------------------------------------------

    enum MatchPhase {
        None,       // matchId not yet used
        Committed,  // roles committed, match can proceed off-chain
        Revealed    // roles revealed + verified, match hash recorded
    }

    struct MatchRecord {
        MatchPhase phase;
        address host;                 // whoever committed the match (msg.sender at commit time)
        uint256 playerCount;
        bytes32[] commitments;        // commitments[i] = hash(role, seat, salt) for seat i
        bool[] revealed;              // revealed[i] = true once seat i's commitment is opened
        bytes32 eventLogHash;         // set at reveal time — hash of the full episodic event log
        uint256 committedAt;
        uint256 revealedAt;
    }

    // ---------------------------------------------------------------
    // Storage
    // ---------------------------------------------------------------

    mapping(bytes32 => MatchRecord) private matches;

    // ---------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------

    event RolesCommitted(
        bytes32 indexed matchId,
        address indexed host,
        uint256 playerCount,
        uint256 timestamp
    );

    event RoleVerified(
        bytes32 indexed matchId,
        uint256 indexed seat,
        uint8 role,
        uint256 timestamp
    );

    event MatchFinalized(
        bytes32 indexed matchId,
        bytes32 eventLogHash,
        uint256 timestamp
    );

    // ---------------------------------------------------------------
    // Errors
    // ---------------------------------------------------------------

    error MatchAlreadyExists();
    error MatchNotCommitted();
    error MatchAlreadyFinalized();
    error ArrayLengthMismatch();
    error SeatOutOfRange();
    error SeatAlreadyRevealed();
    error CommitmentMismatch();
    error NotAllSeatsRevealed();
    error EmptyCommitments();

    // ---------------------------------------------------------------
    // 1. Commit — called once, before the simulated match begins
    // ---------------------------------------------------------------

    /// @notice Commit one hash(role, seat, salt) per player for this match.
    /// @param matchId Caller-chosen unique id for this match (e.g. keccak256 of a UUID string).
    /// @param commitments commitments[seat] = keccak256(abi.encodePacked(role, seat, salt, matchId)).
    ///        Salts must be generated off-chain and kept secret until reveal.
    function commitRoles(bytes32 matchId, bytes32[] calldata commitments) external {
        if (matches[matchId].phase != MatchPhase.None) revert MatchAlreadyExists();
        if (commitments.length == 0) revert EmptyCommitments();

        MatchRecord storage m = matches[matchId];
        m.phase = MatchPhase.Committed;
        m.host = msg.sender;
        m.playerCount = commitments.length;
        m.committedAt = block.timestamp;

        for (uint256 i = 0; i < commitments.length; i++) {
            m.commitments.push(commitments[i]);
            m.revealed.push(false);
        }

        emit RolesCommitted(matchId, msg.sender, commitments.length, block.timestamp);
    }

    // ---------------------------------------------------------------
    // 2. Reveal + finalize — called once, after the simulated match ends
    // ---------------------------------------------------------------

    /// @notice Open every seat's commitment and record the match's event-log hash.
    /// @dev Combined into one call on purpose (see build spec §8.2 — avoid three
    ///      separate transactions when one will do).
    /// @param matchId The match being finalized.
    /// @param roles roles[seat] = that seat's role (e.g. 0 = Crew, 1 = Impostor).
    /// @param seats seats[i] = the seat index being revealed at position i (usually just 0..n-1,
    ///        provided explicitly for clarity and to allow partial/out-of-order reveals).
    /// @param salts salts[i] = the secret salt used at commit time for seats[i].
    /// @param eventLogHash keccak256 (or sha256, see note below) of the full serialized episodic event log.
    function revealAndFinalize(
        bytes32 matchId,
        uint8[] calldata roles,
        uint256[] calldata seats,
        bytes32[] calldata salts,
        bytes32 eventLogHash
    ) external {
        MatchRecord storage m = matches[matchId];

        if (m.phase == MatchPhase.None) revert MatchNotCommitted();
        if (m.phase == MatchPhase.Revealed) revert MatchAlreadyFinalized();
        if (roles.length != seats.length || seats.length != salts.length) {
            revert ArrayLengthMismatch();
        }

        for (uint256 i = 0; i < seats.length; i++) {
            uint256 seat = seats[i];
            if (seat >= m.commitments.length) revert SeatOutOfRange();
            if (m.revealed[seat]) revert SeatAlreadyRevealed();

            bytes32 expected = keccak256(
                abi.encodePacked(roles[i], seat, salts[i], matchId)
            );
            if (expected != m.commitments[seat]) revert CommitmentMismatch();

            m.revealed[seat] = true;
            emit RoleVerified(matchId, seat, roles[i], block.timestamp);
        }

        // Require every seat to have been revealed before finalizing the match.
        // (If you want to support partial reveals across multiple calls, remove
        // this check and call revealAndFinalize once per batch, then a separate
        // finalize() once all seats are revealed — not needed for hackathon scope.)
        for (uint256 i = 0; i < m.revealed.length; i++) {
            if (!m.revealed[i]) revert NotAllSeatsRevealed();
        }

        m.eventLogHash = eventLogHash;
        m.phase = MatchPhase.Revealed;
        m.revealedAt = block.timestamp;

        emit MatchFinalized(matchId, eventLogHash, block.timestamp);
    }

    // ---------------------------------------------------------------
    // 3. Read / verification helpers (for the demo viewer + judges)
    // ---------------------------------------------------------------

    function getMatchPhase(bytes32 matchId) external view returns (MatchPhase) {
        return matches[matchId].phase;
    }

    function getPlayerCount(bytes32 matchId) external view returns (uint256) {
        return matches[matchId].playerCount;
    }

    function getCommitment(bytes32 matchId, uint256 seat) external view returns (bytes32) {
        if (seat >= matches[matchId].commitments.length) revert SeatOutOfRange();
        return matches[matchId].commitments[seat];
    }

    function isSeatRevealed(bytes32 matchId, uint256 seat) external view returns (bool) {
        if (seat >= matches[matchId].revealed.length) revert SeatOutOfRange();
        return matches[matchId].revealed[seat];
    }

    function getEventLogHash(bytes32 matchId) external view returns (bytes32) {
        return matches[matchId].eventLogHash;
    }

    function getMatchTimestamps(bytes32 matchId)
        external
        view
        returns (uint256 committedAt, uint256 revealedAt)
    {
        MatchRecord storage m = matches[matchId];
        return (m.committedAt, m.revealedAt);
    }

    /// @notice Convenience check anyone (a judge, another player) can call to
    ///         independently verify a claimed (role, seat, salt) against the
    ///         on-chain commitment, without trusting the host or the UI.
    function verifyCommitment(
        bytes32 matchId,
        uint8 role,
        uint256 seat,
        bytes32 salt
    ) external view returns (bool) {
        if (seat >= matches[matchId].commitments.length) revert SeatOutOfRange();
        bytes32 expected = keccak256(abi.encodePacked(role, seat, salt, matchId));
        return expected == matches[matchId].commitments[seat];
    }
}
