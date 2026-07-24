// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title IArbExecutor
/// @notice Stable entrypoint for the L2 flash-loan arbitrage executor. A route is a compact byte
///         string (see docs/specs/09-route-codec.md); execution is atomic and reverts unless the
///         realized profit in the profit token is at least `minProfit`.
interface IArbExecutor {
    /// @notice Emitted once per successful arbitrage.
    /// @param routeHash keccak256 of the executed route bytes.
    /// @param token     the profit token (borrowed and repaid).
    /// @param profit    realized profit in `token`.
    /// @param gasUsed   gas consumed by the execution (best-effort).
    event ArbExecuted(bytes32 indexed routeHash, address indexed token, uint256 profit, uint256 gasUsed);

    /// @notice Caller is not an authorized operator.
    error NotOperator();
    /// @notice The contract is paused.
    error Paused();
    /// @notice `block.timestamp` exceeded the route deadline.
    error Expired();
    /// @notice Reentrant entry detected.
    error Reentrancy();
    /// @notice The route bytes are malformed. `code` identifies the failed check.
    error BadRoute(uint256 code);
    /// @notice Functionality not yet implemented in this build (walking skeleton).
    error NotImplemented();
    /// @notice Realized profit was below the caller's minimum. Whole tx reverts.
    error Unprofitable(uint256 got, uint256 minProfit);

    /// @notice Execute an atomic arbitrage described by `route`.
    /// @param route     compact byte-encoded route (docs/specs/09-route-codec.md).
    /// @param minProfit minimum acceptable profit in the profit token; reverts if not met.
    /// @param deadline  unix seconds; reverts if `block.timestamp > deadline`.
    /// @param recipient where realized profit is swept.
    /// @return profit   realized profit in the profit token.
    function execute(bytes calldata route, uint256 minProfit, uint256 deadline, address recipient)
        external
        returns (uint256 profit);
}
