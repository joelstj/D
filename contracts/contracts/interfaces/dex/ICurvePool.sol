// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

/// @title ICurvePool
/// @notice Minimal Curve StableSwap surface (int128 coin indices).
/// @dev Called via low-level `call` by the executor so that pools whose
///      `exchange` returns nothing (older 3pool-style deployments) do not
///      revert on return-data decoding. Crypto pools that use uint256 indices
///      should be routed through DexType.GENERIC.
interface ICurvePool {
    /// @notice Exchanges `dx` of coin `i` for coin `j`, requiring at least `min_dy` out.
    function exchange(int128 i, int128 j, uint256 dx, uint256 min_dy) external returns (uint256);
}
