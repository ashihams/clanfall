// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

interface IMatchLedger {
    function getMatchPhase(bytes32 matchId) external view returns (uint8);
}

/// @title ClanfallLeaderboard
/// @notice Opt-in, player-published win/loss record. Entirely separate from
///         match memory (which is wiped every match): nothing is written here
///         unless the player's own wallet sends the transaction. The host only
///         signs an attestation of the result, so a player cannot claim a win
///         they did not get.
contract ClanfallLeaderboard {
    uint8 private constant PHASE_REVEALED = 2;

    struct Record {
        uint64 wins;
        uint64 losses;
        uint64 lastSubmittedAt;
    }

    IMatchLedger public immutable ledger;
    address public immutable attestor;

    mapping(address => Record) private records;
    mapping(bytes32 => mapping(address => bool)) public hasSubmitted;
    address[] private players;

    event ResultSubmitted(address indexed player, bytes32 indexed matchId, bool won, uint64 wins, uint64 losses);

    error MatchNotFinalized();
    error AlreadySubmitted();
    error InvalidAttestation();

    constructor(address ledger_, address attestor_) {
        ledger = IMatchLedger(ledger_);
        attestor = attestor_;
    }

    /// @notice The message the host signs (EIP-191 personal_sign) for one player's result.
    function resultDigest(bytes32 matchId, address player, bool won) public view returns (bytes32) {
        return keccak256(abi.encodePacked(address(this), block.chainid, matchId, player, won));
    }

    /// @notice Called by the player themselves (msg.sender) to publish one match result.
    function optInAndSubmit(bytes32 matchId, bool won, bytes calldata attestation) external {
        if (ledger.getMatchPhase(matchId) != PHASE_REVEALED) revert MatchNotFinalized();
        if (hasSubmitted[matchId][msg.sender]) revert AlreadySubmitted();

        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(resultDigest(matchId, msg.sender, won));
        (address signer, ECDSA.RecoverError err,) = ECDSA.tryRecover(digest, attestation);
        if (err != ECDSA.RecoverError.NoError || signer != attestor) revert InvalidAttestation();

        hasSubmitted[matchId][msg.sender] = true;
        Record storage r = records[msg.sender];
        if (r.wins == 0 && r.losses == 0) players.push(msg.sender);
        if (won) r.wins += 1;
        else r.losses += 1;
        r.lastSubmittedAt = uint64(block.timestamp);
        emit ResultSubmitted(msg.sender, matchId, won, r.wins, r.losses);
    }

    function getRecord(address player) external view returns (uint64 wins, uint64 losses, uint64 lastSubmittedAt) {
        Record storage r = records[player];
        return (r.wins, r.losses, r.lastSubmittedAt);
    }

    function playerCount() external view returns (uint256) {
        return players.length;
    }

    function playerAt(uint256 index) external view returns (address) {
        return players[index];
    }
}
