// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {ERC721URIStorage} from "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

interface IMatchLedger {
    function getMatchPhase(bytes32 matchId) external view returns (uint8);
    function getEventLogHash(bytes32 matchId) external view returns (bytes32);
}

/// @title MatchPassport
/// @notice One ERC-721 per finished Clanfall match. A passport can only be
///         minted for a match that MatchLedger has already revealed, and only
///         with the exact event-log hash recorded there, so the token is
///         anchored to the commit-reveal record rather than standing alone.
contract MatchPassport is ERC721URIStorage, Ownable {
    uint8 private constant PHASE_REVEALED = 2;

    IMatchLedger public immutable ledger;
    uint256 public totalMinted;

    mapping(bytes32 => uint256) public tokenOfMatch;
    mapping(uint256 => bytes32) public matchOfToken;
    mapping(uint256 => bytes32) public eventLogHashOf;

    event PassportMinted(uint256 indexed tokenId, bytes32 indexed matchId, address indexed to, bytes32 eventLogHash);

    error MatchNotFinalized();
    error EventLogHashMismatch();
    error PassportAlreadyMinted();

    constructor(address ledger_) ERC721("Clanfall Match Passport", "CLANPASS") Ownable(msg.sender) {
        ledger = IMatchLedger(ledger_);
    }

    /// @param uri data: URI (or URL) of the ERC-721 metadata JSON: summary, sigil image, event-log hash.
    function mintPassport(address to, bytes32 matchId, bytes32 eventLogHash, string calldata uri)
        external
        onlyOwner
        returns (uint256 tokenId)
    {
        if (ledger.getMatchPhase(matchId) != PHASE_REVEALED) revert MatchNotFinalized();
        if (ledger.getEventLogHash(matchId) != eventLogHash) revert EventLogHashMismatch();
        if (tokenOfMatch[matchId] != 0) revert PassportAlreadyMinted();

        tokenId = ++totalMinted;
        tokenOfMatch[matchId] = tokenId;
        matchOfToken[tokenId] = matchId;
        eventLogHashOf[tokenId] = eventLogHash;
        _mint(to, tokenId);
        _setTokenURI(tokenId, uri);
        emit PassportMinted(tokenId, matchId, to, eventLogHash);
    }
}
