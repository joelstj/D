// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IArbExecutor} from "../interfaces/IArbExecutor.sol";

/// @title ArbExecutor — walking skeleton
/// @notice Minimal, hardened entrypoint for the L2 flash-loan arbitrage engine. Access control,
///         pause, reentrancy protection, deadline and route-header validation are REAL and tested.
///         Flash borrowing, hop dispatch, the profit invariant, and the full RouteCodec arrive in
///         Phase 2/3 (see docs/BUILD_PLAN.md and ralph/BACKLOG.md).
/// @dev    Intentionally dependency-free so the baseline compiles before any lib is installed.
///         The storage reentrancy guard is upgraded to transient storage on Cancun chains in P2-T3.
contract ArbExecutor is IArbExecutor {
    // --- constants ---------------------------------------------------------
    uint8 internal constant ROUTE_VERSION = 0x01;
    uint8 internal constant MAX_HOPS = 8;

    // --- admin events / errors (entrypoint ones live in IArbExecutor) -------
    event OwnershipTransferStarted(address indexed from, address indexed to);
    event OwnershipTransferred(address indexed from, address indexed to);
    event OperatorSet(address indexed operator, bool allowed);
    event PausedSet(bool paused);
    event Rescued(address indexed token, address indexed to, uint256 amount);

    error NotOwner();
    error NotPendingOwner();
    error ZeroAddress();
    error TransferFailed();

    // --- state -------------------------------------------------------------
    address public owner;
    address public pendingOwner;
    bool public paused;
    mapping(address => bool) public isOperator;

    /// @dev 1 = unlocked, 2 = locked. Non-zero baseline avoids repeated zero->nonzero SSTOREs.
    uint256 private _lock;

    // --- modifiers ---------------------------------------------------------
    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyOperator() {
        if (msg.sender != owner && !isOperator[msg.sender]) revert NotOperator();
        _;
    }

    modifier nonReentrant() {
        if (_lock == 2) revert Reentrancy();
        _lock = 2;
        _;
        _lock = 1;
    }

    // --- construction ------------------------------------------------------
    constructor(address initialOwner) {
        if (initialOwner == address(0)) revert ZeroAddress();
        owner = initialOwner;
        _lock = 1;
        emit OwnershipTransferred(address(0), initialOwner);
    }

    // --- admin -------------------------------------------------------------
    function setOperator(address op, bool allowed) external onlyOwner {
        isOperator[op] = allowed;
        emit OperatorSet(op, allowed);
    }

    function setPaused(bool p) external onlyOwner {
        paused = p;
        emit PausedSet(p);
    }

    /// @notice Two-step ownership transfer (step 1).
    function transferOwnership(address newOwner) external onlyOwner {
        pendingOwner = newOwner;
        emit OwnershipTransferStarted(owner, newOwner);
    }

    /// @notice Two-step ownership transfer (step 2).
    function acceptOwnership() external {
        if (msg.sender != pendingOwner) revert NotPendingOwner();
        emit OwnershipTransferred(owner, pendingOwner);
        owner = pendingOwner;
        pendingOwner = address(0);
    }

    /// @notice Owner rescue for tokens accidentally sent to this contract.
    function rescue(address token, address to, uint256 amount) external onlyOwner {
        (bool ok, bytes memory ret) = token.call(abi.encodeWithSelector(0xa9059cbb, to, amount));
        if (!ok || (ret.length != 0 && !abi.decode(ret, (bool)))) revert TransferFailed();
        emit Rescued(token, to, amount);
    }

    // --- entrypoint --------------------------------------------------------
    /// @inheritdoc IArbExecutor
    function execute(bytes calldata route, uint256 minProfit, uint256 deadline, address recipient)
        external
        override
        onlyOperator
        nonReentrant
        returns (uint256 profit)
    {
        if (paused) revert Paused();
        if (block.timestamp > deadline) revert Expired();

        (uint8 version, uint8 hopCount) = _readHeader(route);
        if (version != ROUTE_VERSION) revert BadRoute(0);
        if (hopCount == 0 || hopCount > MAX_HOPS) revert BadRoute(1);

        // Phase 2/3: select+borrow via IFlashProvider, dispatch hops via IDexAdapter, repay, then
        // enforce `profit >= minProfit` and sweep to `recipient`. Until then, fail loudly.
        recipient;
        minProfit;
        revert NotImplemented();
    }

    // --- internal ----------------------------------------------------------
    /// @dev Reads header bytes 0 (version) and 2 (hopCount) straight from calldata. Byte 1 is flags.
    ///      Demonstrates the Yul calldata-parsing approach the full RouteCodec (P2-T1) extends.
    ///      Layout: docs/specs/09-route-codec.md.
    function _readHeader(bytes calldata route) internal pure returns (uint8 version, uint8 hopCount) {
        if (route.length < 3) revert BadRoute(2);
        assembly {
            let word := calldataload(route.offset)
            version := byte(0, word)
            hopCount := byte(2, word)
        }
    }
}
