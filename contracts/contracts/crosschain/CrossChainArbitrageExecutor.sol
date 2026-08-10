// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {SwapStep} from "../libraries/ArbTypes.sol";
import {DexRouter} from "../libraries/DexRouter.sol";
import {IBridgeAdapter} from "../interfaces/IBridgeAdapter.sol";

/// @title CrossChainArbitrageExecutor
/// @author L2_on-chain
/// @notice Inventory-based executor for cross-chain arbitrage across the
///         supported L2s.
///
/// @dev ⚠️  IMPORTANT — READ BEFORE USE ⚠️
///
///      Cross-chain arbitrage CANNOT be atomic and CANNOT use a flash loan.
///      A flash loan must be borrowed and repaid inside a single transaction on
///      a single chain; a transaction cannot span two chains. Any product that
///      claims "atomic cross-chain flash-loan arbitrage" is misdescribing what
///      the EVM can do.
///
///      The realistic, production model — implemented here — is INVENTORY-BASED
///      and executes in TWO separate transactions on TWO chains:
///
///        Tx A (source chain): swap idle inventory into a bridgeable asset and
///                             send it across a bridge to the destination.
///        Tx B (dest chain):   once the bridged funds arrive (seconds to
///                             minutes later), swap them into the target asset.
///
///      Between Tx A and Tx B your capital is IN FLIGHT and exposed to price
///      movement and bridge risk. The opportunity is NOT guaranteed to still
///      exist when the funds land. Mitigations: use fast intent/solver bridges
///      (e.g. Across) for near-instant settlement, keep pre-positioned
///      inventory on both chains so each leg is independent, size conservatively,
///      and treat the two legs as a hedged position rather than a locked-in
///      arbitrage. For truly atomic profit, use the single-chain
///      FlashLoanArbitrage engine instead.
///
///      This contract deliberately holds inventory (unlike the flash-loan
///      engine, which never should) and is guarded by roles + pause + rescue.
///
///      Yul scope: both legs' hot loop (`_walkRoute`) already runs entirely
///      through `DexRouter.balanceOf`/`DexRouter.execute`, which carry their
///      own hand-optimised Yul (see DexRouter.sol) — every swap this contract
///      makes already benefits from that. There is no *additional* low-level
///      call this contract makes itself (the bridge call takes a caller-
///      supplied `bytes options` of unknown shape, so it can't be hand-encoded
///      the way DexRouter's fixed-shape swap calls can) worth converting —
///      considered, not skipped.
///
///      `_walkRoute`'s own `SwapStep memory step = steps[i]` (a calldata-to-
///      memory struct decode per hop, unlike `FlashLoanArbitrage._runRoute`'s
///      equivalent line, which is a free pointer copy since its `steps` is
///      already memory) looks like a textbook fit for
///      `docs/specs/07-gas-and-yul.md`'s "Route decoding... no memory copies"
///      pattern, and was concretely tried: `DexRouter.execute` was refactored
///      from a `SwapStep memory` parameter to twelve scalar parameters (so
///      each caller passes only the fields it already has, calldata or
///      memory, with no struct materialisation). Measured with a real 2-hop
///      `executeDestinationLeg` call, before/after on an otherwise-identical
///      commit (`git stash`, not a guess): 161,240 gas before, 162,209 after
///      — a reproducible **+969 gas regression**, not a win. The struct-decode
///      savings were real but smaller than the added cost of marshalling
///      twelve stack arguments across the internal-call boundary instead of
///      one struct pointer, for the common case (V2/V3_SINGLE hops, where
///      `data` is empty and there was little decode cost to save in the
///      first place). Reverted rather than shipped, per this file's own gas
///      rule: "optimize against a benchmark, never on vibes." Recorded here
///      so a future session doesn't re-attempt the same measured-losing
///      change from scratch.
contract CrossChainArbitrageExecutor is AccessControl, ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    bytes32 public constant EXECUTOR_ROLE = keccak256("EXECUTOR_ROLE");
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    // ---------------- Bridge / sibling-executor allowlists ---------------- //

    /// @notice Bridge adapters `executeSourceLeg` is allowed to call. Empty
    ///         (deny-all) until a guardian explicitly allows one.
    /// @dev `executeSourceLeg` takes `bridgeAdapter` as a caller-supplied
    ///      address, `forceApprove`s it for the ENTIRE held balance of
    ///      `bridgeToken`, and calls it with `msg.value` — structurally the
    ///      same shape of risk as `FlashLoanArbitrage`'s `DexType.GENERIC`
    ///      router (see that contract's `allowedGenericRouters`). Without
    ///      this allowlist, a compromised EXECUTOR_ROLE hot key could pass a
    ///      malicious "adapter" contract and drain the full held inventory of
    ///      `bridgeToken` (plus any attached native value) in one call. See
    ///      `executeSourceLeg`.
    mapping(address => bool) public allowedBridgeAdapters;

    /// @notice Known-good executor address per destination chain id, set by a
    ///         guardian. address(0) (the default for every chain id) means
    ///         "no sibling registered" — `executeSourceLeg` then imposes no
    ///         constraint on `dstRecipient` for that chain, exactly as before
    ///         this registry existed.
    /// @dev Guards against sending bridged funds to the wrong destination-
    ///      chain address — without it, an operator typo or a compromised
    ///      EXECUTOR_ROLE key has no on-chain check that `dstRecipient` is
    ///      where this deployment actually expects funds to land. Settable
    ///      back to address(0) deliberately (no `ZeroAddress` revert in that
    ///      setter) so a guardian can intentionally unregister/unconstrain a
    ///      chain. See `executeSourceLeg`.
    mapping(uint256 => address) public siblingExecutor;

    error ZeroAddress();
    error EmptyRoute();
    error InsufficientBridgeAmount(uint256 produced, uint256 minBridge);
    error InsufficientLegOutput(uint256 produced, uint256 minOut);
    error RouteSeedMismatch();
    error ZeroRouteInput();
    error NothingToRescue();
    error BridgeAdapterNotAllowed(address adapter);
    error SiblingMismatch(address dstRecipient, address expected);

    /// @notice Emitted after the source leg swaps and dispatches funds to a bridge.
    event SourceLegDispatched(
        address indexed bridgeAdapter,
        address indexed bridgeToken,
        uint256 indexed dstChainId,
        address dstRecipient,
        uint256 amountBridged
    );

    /// @notice Emitted after the destination leg swaps bridged funds into the target asset.
    event DestinationLegSettled(
        address indexed inputToken, address indexed outputToken, uint256 amountIn, uint256 amountOut
    );

    event Rescued(address indexed token, address indexed to, uint256 amount);

    /// @notice Emitted when a guardian changes a bridge adapter's allowlist status.
    event BridgeAdapterAllowlistUpdated(address indexed adapter, bool allowed);

    /// @notice Emitted when a guardian sets (or clears) the known-good
    ///         sibling executor address for a destination chain id.
    event SiblingExecutorUpdated(uint256 indexed chainId, address indexed executorAddr);

    /// @param admin Address granted admin, guardian and executor roles.
    constructor(address admin) {
        if (admin == address(0)) revert ZeroAddress();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(GUARDIAN_ROLE, admin);
        _grantRole(EXECUTOR_ROLE, admin);
    }

    /// @notice Source leg: swap held inventory along `steps`, then bridge the
    ///         resulting `bridgeToken` to `dstRecipient` on `dstChainId`.
    /// @param steps Route producing `bridgeToken` from held inventory (may be empty to bridge as-is).
    /// @param seedToken The token the route starts from (ignored if `steps` empty).
    /// @param seedAmount The amount of `seedToken` to feed into the route (or bridge directly).
    /// @param bridgeAdapter Adapter wrapping the chosen bridge. Must be
    ///        allowlisted via `setBridgeAdapterAllowed` (deny-all by default)
    ///        — see `allowedBridgeAdapters`.
    /// @param bridgeToken The token to bridge (route output).
    /// @param minBridgeAmount Revert unless at least this much `bridgeToken` is produced.
    /// @param dstChainId Destination chain id.
    /// @param dstRecipient Recipient on the destination chain (usually the
    ///        sibling executor). If a sibling is registered for `dstChainId`
    ///        via `setSiblingExecutor`, this must match it exactly.
    /// @param bridgeOptions Bridge-specific options (relayer fee, slippage, ...).
    /// @dev Non-atomic across chains — see the contract-level notice.
    function executeSourceLeg(
        SwapStep[] calldata steps,
        address seedToken,
        uint256 seedAmount,
        address bridgeAdapter,
        address bridgeToken,
        uint256 minBridgeAmount,
        uint256 dstChainId,
        address dstRecipient,
        bytes calldata bridgeOptions
    ) external payable nonReentrant whenNotPaused onlyRole(EXECUTOR_ROLE) {
        if (bridgeAdapter == address(0) || bridgeToken == address(0) || dstRecipient == address(0)) {
            revert ZeroAddress();
        }
        if (!allowedBridgeAdapters[bridgeAdapter]) revert BridgeAdapterNotAllowed(bridgeAdapter);
        address expected = siblingExecutor[dstChainId];
        if (expected != address(0) && dstRecipient != expected) {
            revert SiblingMismatch(dstRecipient, expected);
        }

        if (steps.length != 0) {
            _walkRoute(steps, seedToken, seedAmount);
        }

        uint256 amount = DexRouter.balanceOf(bridgeToken, address(this));
        if (amount < minBridgeAmount || amount == 0) {
            revert InsufficientBridgeAmount(amount, minBridgeAmount);
        }

        IERC20(bridgeToken).forceApprove(bridgeAdapter, amount);
        IBridgeAdapter(bridgeAdapter).bridge{value: msg.value}(
            bridgeToken, amount, dstChainId, dstRecipient, bridgeOptions
        );

        emit SourceLegDispatched(bridgeAdapter, bridgeToken, dstChainId, dstRecipient, amount);
    }

    /// @notice Destination leg: swap bridged funds along `steps` into a target asset.
    /// @param steps Route from the bridged token to the desired output token.
    /// @param inputToken The bridged token the route starts from.
    /// @param inputAmount The amount to feed in (0 => use full held balance of inputToken).
    /// @param minOut Revert unless at least this much of the final token is produced.
    /// @dev Non-atomic across chains — see the contract-level notice.
    function executeDestinationLeg(SwapStep[] calldata steps, address inputToken, uint256 inputAmount, uint256 minOut)
        external
        nonReentrant
        whenNotPaused
        onlyRole(EXECUTOR_ROLE)
    {
        if (steps.length == 0) revert EmptyRoute();
        uint256 amountIn = inputAmount == 0 ? DexRouter.balanceOf(inputToken, address(this)) : inputAmount;

        address outputToken = steps[steps.length - 1].tokenOut;
        uint256 outBefore = DexRouter.balanceOf(outputToken, address(this));

        _walkRoute(steps, inputToken, amountIn);

        uint256 produced = DexRouter.balanceOf(outputToken, address(this)) - outBefore;
        if (produced < minOut) revert InsufficientLegOutput(produced, minOut);

        emit DestinationLegSettled(inputToken, outputToken, amountIn, produced);
    }

    /// @dev Feed-forward route walker shared by both legs.
    function _walkRoute(SwapStep[] calldata steps, address seedToken, uint256 seedAmount) private {
        uint256 n = steps.length;
        if (n == 0) revert EmptyRoute();
        // Validate the route actually starts at the seed token.
        if (steps[0].tokenIn != seedToken) revert RouteSeedMismatch();

        uint256 amountIn = seedAmount;
        for (uint256 i; i < n;) {
            SwapStep memory step = steps[i];
            uint256 bal = DexRouter.balanceOf(step.tokenIn, address(this));
            if (amountIn > bal) amountIn = bal;
            if (amountIn == 0) revert ZeroRouteInput();
            amountIn = DexRouter.execute(step, amountIn, i);
            unchecked {
                ++i;
            }
        }
    }

    // ------------------------------- Admin -------------------------------- //

    function pause() external onlyRole(GUARDIAN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(GUARDIAN_ROLE) {
        _unpause();
    }

    /// @notice Allow or revoke a bridge adapter address for `executeSourceLeg`.
    /// @dev GUARDIAN_ROLE, not EXECUTOR_ROLE — mirrors
    ///      `FlashLoanArbitrage.setGenericRouterAllowed`: the hot bot key
    ///      that picks the bridge adapter each call must not also be able to
    ///      expand what it's allowed to call.
    function setBridgeAdapterAllowed(address adapter, bool allowed) external onlyRole(GUARDIAN_ROLE) {
        if (adapter == address(0)) revert ZeroAddress();
        allowedBridgeAdapters[adapter] = allowed;
        emit BridgeAdapterAllowlistUpdated(adapter, allowed);
    }

    /// @notice Set (or clear, via address(0)) the known-good sibling executor
    ///         address for `chainId`.
    /// @dev No `ZeroAddress` revert here: setting back to address(0) is a
    ///      valid "unregister/unconstrain this chain" state, not a mistake.
    function setSiblingExecutor(uint256 chainId, address executorAddr) external onlyRole(GUARDIAN_ROLE) {
        siblingExecutor[chainId] = executorAddr;
        emit SiblingExecutorUpdated(chainId, executorAddr);
    }

    /// @notice Sweeps an ERC20 balance to `to` (inventory or stuck funds).
    function rescueTokens(address token, address to, uint256 amount) external onlyRole(GUARDIAN_ROLE) {
        uint256 bal = IERC20(token).balanceOf(address(this));
        uint256 sweep = amount == 0 ? bal : amount;
        if (sweep == 0 || sweep > bal) revert NothingToRescue();
        IERC20(token).safeTransfer(to, sweep);
        emit Rescued(token, to, sweep);
    }

    /// @notice Sweeps native gas token.
    function rescueETH(address payable to, uint256 amount) external onlyRole(GUARDIAN_ROLE) {
        uint256 bal = address(this).balance;
        uint256 sweep = amount == 0 ? bal : amount;
        if (sweep == 0 || sweep > bal) revert NothingToRescue();
        (bool ok,) = to.call{value: sweep}("");
        if (!ok) revert NothingToRescue();
        emit Rescued(address(0), to, sweep);
    }

    receive() external payable {}
}
