// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {ArbParams, SwapStep, FlashProvider, DexType} from "./libraries/ArbTypes.sol";
import {DexRouter} from "./libraries/DexRouter.sol";
import {OptimalArbitrage} from "./libraries/OptimalArbitrage.sol";
import {IAaveV3Pool, IAaveFlashLoanSimpleReceiver} from "./interfaces/IAaveV3Pool.sol";
import {IBalancerVault, IBalancerFlashLoanRecipient} from "./interfaces/IBalancerVault.sol";

/// @title FlashLoanArbitrage
/// @author L2_on-chain
/// @notice Atomic, single-transaction flash-loan arbitrage engine for EVM L2s
///         (Optimism, Base, Arbitrum One, Ink, Unichain, Polygon).
/// @dev One deployment per chain. The flash-loan provider addresses are set
///      once as immutables; DEX router addresses are supplied per-call inside
///      the route, so a single contract works with any venue on its chain
///      without upgrades.
///
///      Execution model (fully atomic — the whole thing reverts on any failure,
///      so an unprofitable attempt costs only gas):
///        1. Borrow `amount` of `asset` from Aave V3 or Balancer V2.
///        2. Walk the route, swapping the entire running balance forward at each
///           hop (2-hop, 3-hop, N-hop, cross-DEX — all just longer routes).
///        3. Require net profit in `asset` >= `minProfit`, else revert.
///        4. Repay the loan (+premium) and forward profit to `profitReceiver`.
///
///      Dynamic loan sizing: because every hop swaps the full running balance,
///      the same route works at any size. `quoteOptimalTwoHopV2` returns the
///      liquidity-aware profit-maximising size for the classic two-pool case;
///      bots pass that (or their own sizing) as `amount`.
///
///      Security: only EXECUTOR_ROLE can start an arbitrage; both flash
///      callbacks verify the caller is the expected provider AND that this
///      contract itself initiated the loan (guarding against griefers who
///      target the contract with an unsolicited flash loan).
contract FlashLoanArbitrage is
    IAaveFlashLoanSimpleReceiver,
    IBalancerFlashLoanRecipient,
    AccessControl,
    ReentrancyGuard,
    Pausable
{
    using SafeERC20 for IERC20;

    // --------------------------------------------------------------------- //
    //                               Roles                                   //
    // --------------------------------------------------------------------- //

    /// @notice Role permitted to trigger arbitrage executions (your bot's key).
    bytes32 public constant EXECUTOR_ROLE = keccak256("EXECUTOR_ROLE");
    /// @notice Role permitted to pause, unpause and rescue tokens (your admin/multisig).
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    // --------------------------------------------------------------------- //
    //                            Immutables                                 //
    // --------------------------------------------------------------------- //

    /// @notice Aave V3 Pool for this chain (address(0) if Aave is not used here).
    address public immutable AAVE_POOL;
    /// @notice Balancer V2 Vault for this chain (address(0) if Balancer is not used here).
    address public immutable BALANCER_VAULT;

    // --------------------------------------------------------------------- //
    //                          Reentrancy latch                             //
    // --------------------------------------------------------------------- //

    uint256 private constant _CB_IDLE = 1;
    uint256 private constant _CB_ARMED = 2;
    /// @dev Armed immediately before calling a flash provider, checked and
    ///      disarmed inside the callback. Blocks unsolicited callbacks.
    uint256 private _callbackState = _CB_IDLE;

    // --------------------------------------------------------------------- //
    //                    GENERIC-hop router allowlist                       //
    // --------------------------------------------------------------------- //

    /// @notice Routers a GENERIC-type hop is allowed to call. Empty (deny-all)
    ///         until a guardian explicitly allows one.
    /// @dev GENERIC exists so an exotic venue can be integrated without a
    ///      contract upgrade — but unlike the typed dex types (which only ever
    ///      call a fixed, well-known selector shape with the output recipient
    ///      hardcoded to `address(this)`), GENERIC hands `step.router` fully
    ///      attacker-controlled calldata. Without this allowlist, a route
    ///      whose `dexType == GENERIC` could target ANY address with ANY
    ///      calldata — e.g. `transfer(attacker, hugeAmount)` on a completely
    ///      unrelated token this contract happens to hold — draining far more
    ///      than the current hop's `amountIn`, while `steps[0].tokenIn`/
    ///      `steps[n-1].tokenOut` and the overall profit check (which only
    ///      look at the declared route and the balance delta of `p.asset`)
    ///      never notice, because those checks trust the declared route, not
    ///      what the calldata actually does. See `_runRoute`.
    mapping(address => bool) public allowedGenericRouters;

    // --------------------------------------------------------------------- //
    //                               Errors                                  //
    // --------------------------------------------------------------------- //

    error ProviderNotConfigured();
    error InvalidProvider();
    error DeadlineExpired();
    error InvalidRoute();
    error RouteAssetMismatch();
    error RouteNotContiguous(uint256 step);
    error ZeroAmount();
    error UnexpectedCaller(address caller);
    error UnexpectedInitiator(address initiator);
    error CallbackNotArmed();
    error CallbackAssetMismatch();
    error InsufficientProfit(uint256 generated, uint256 required);
    error NothingToRescue();
    error GenericRouterNotAllowed(address router);

    // --------------------------------------------------------------------- //
    //                               Events                                  //
    // --------------------------------------------------------------------- //

    /// @notice Emitted on every successful arbitrage. Historical PnL lives in
    ///         logs, not storage (gas rule: events over storage).
    event ArbitrageExecuted(
        address indexed asset,
        FlashProvider indexed provider,
        address indexed profitReceiver,
        uint256 amountBorrowed,
        uint256 amountOwed,
        uint256 profit,
        uint256 hops
    );

    /// @notice Emitted when tokens are swept out by a guardian.
    event Rescued(address indexed token, address indexed to, uint256 amount);

    /// @notice Emitted when a guardian changes a GENERIC-hop router's allowlist status.
    event GenericRouterAllowlistUpdated(address indexed router, bool allowed);

    // --------------------------------------------------------------------- //
    //                            Construction                               //
    // --------------------------------------------------------------------- //

    /// @param aavePool The Aave V3 Pool address for this chain, or address(0).
    /// @param balancerVault The Balancer V2 Vault address for this chain, or address(0).
    /// @param admin The address granted admin, guardian and executor roles.
    /// @dev At least one provider must be configured.
    constructor(address aavePool, address balancerVault, address admin) {
        if (aavePool == address(0) && balancerVault == address(0)) revert ProviderNotConfigured();
        if (admin == address(0)) revert InvalidProvider();

        AAVE_POOL = aavePool;
        BALANCER_VAULT = balancerVault;

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(GUARDIAN_ROLE, admin);
        _grantRole(EXECUTOR_ROLE, admin);
    }

    // --------------------------------------------------------------------- //
    //                          Main entry point                             //
    // --------------------------------------------------------------------- //

    /// @notice Executes one atomic flash-loan arbitrage.
    /// @param p The full arbitrage parameter set (see ArbTypes.ArbParams).
    /// @dev Reverts (costing only gas) unless the route yields at least
    ///      `p.minProfit` in `p.asset` after repaying the loan. Simulate with
    ///      `eth_call` first; the InsufficientProfit error reports the shortfall.
    function executeArbitrage(ArbParams calldata p) external nonReentrant whenNotPaused onlyRole(EXECUTOR_ROLE) {
        if (block.timestamp > p.deadline) revert DeadlineExpired();
        uint256 nSteps = p.steps.length;
        if (nSteps < 2) revert InvalidRoute();
        if (p.amount == 0) revert ZeroAmount();
        if (p.steps[0].tokenIn != p.asset || p.steps[nSteps - 1].tokenOut != p.asset) {
            revert RouteAssetMismatch();
        }
        // Every hop must consume exactly what the previous hop produced. Without
        // this, an intermediate hop could name a `tokenIn` the prior hop did NOT
        // produce but that the contract happens to hold (a parked/airdropped
        // token). `_runRoute`'s "spend up to the live balance" cap would then
        // vacuum that entire unrelated holding into the trade, and `_settle`
        // (which measures profit only as the balance delta of `p.asset`) would
        // hand it to `profitReceiver` — a drain of held non-`asset` tokens by a
        // compromised EXECUTOR, reachable with plain typed dexes and thus
        // bypassing the GENERIC-router allowlist entirely. Contiguity confines
        // the route to the borrowed asset and its forward-swapped proceeds.
        for (uint256 i = 1; i < nSteps;) {
            if (p.steps[i].tokenIn != p.steps[i - 1].tokenOut) revert RouteNotContiguous(i);
            unchecked {
                ++i;
            }
        }

        // Snapshot pre-loan balance so idle/parked `asset` is never counted as
        // profit and is always preserved across the trade.
        uint256 preBalance = DexRouter.balanceOf(p.asset, address(this));
        bytes memory params = abi.encode(p, preBalance, msg.sender);

        _callbackState = _CB_ARMED;
        if (p.provider == FlashProvider.AAVE_V3) {
            if (AAVE_POOL == address(0)) revert ProviderNotConfigured();
            IAaveV3Pool(AAVE_POOL).flashLoanSimple(address(this), p.asset, p.amount, params, 0);
        } else if (p.provider == FlashProvider.BALANCER_V2) {
            if (BALANCER_VAULT == address(0)) revert ProviderNotConfigured();
            address[] memory tokens = new address[](1);
            uint256[] memory amounts = new uint256[](1);
            tokens[0] = p.asset;
            amounts[0] = p.amount;
            IBalancerVault(BALANCER_VAULT).flashLoan(address(this), tokens, amounts, params);
        } else {
            revert InvalidProvider();
        }
        // Defensive: the callback disarms, but ensure a clean state regardless.
        _callbackState = _CB_IDLE;
    }

    // --------------------------------------------------------------------- //
    //                          Aave V3 callback                             //
    // --------------------------------------------------------------------- //

    /// @inheritdoc IAaveFlashLoanSimpleReceiver
    function executeOperation(address asset, uint256 amount, uint256 premium, address initiator, bytes calldata params)
        external
        override
        returns (bool)
    {
        if (msg.sender != AAVE_POOL) revert UnexpectedCaller(msg.sender);
        if (initiator != address(this)) revert UnexpectedInitiator(initiator);
        _consumeArmed();

        uint256 owed = amount + premium;
        _settle(asset, amount, owed, params, FlashProvider.AAVE_V3);

        // Aave pulls `owed` via transferFrom after this returns.
        IERC20(asset).forceApprove(AAVE_POOL, owed);
        return true;
    }

    // --------------------------------------------------------------------- //
    //                        Balancer V2 callback                           //
    // --------------------------------------------------------------------- //

    /// @inheritdoc IBalancerFlashLoanRecipient
    function receiveFlashLoan(
        address[] calldata tokens,
        uint256[] calldata amounts,
        uint256[] calldata feeAmounts,
        bytes calldata params
    ) external override {
        if (msg.sender != BALANCER_VAULT) revert UnexpectedCaller(msg.sender);
        _consumeArmed();

        address asset = tokens[0];
        uint256 amount = amounts[0];
        uint256 owed = amount + feeAmounts[0];
        _settle(asset, amount, owed, params, FlashProvider.BALANCER_V2);

        // Balancer is repaid by transferring the owed amount back to the Vault.
        IERC20(asset).safeTransfer(BALANCER_VAULT, owed);
    }

    // --------------------------------------------------------------------- //
    //                        Shared settlement logic                        //
    // --------------------------------------------------------------------- //

    /// @dev Runs the route, enforces min-profit, and forwards profit. Leaves
    ///      exactly `owed` (plus any pre-existing parked balance) in the
    ///      contract for the caller to repay.
    function _settle(address asset, uint256 amount, uint256 owed, bytes calldata params, FlashProvider provider)
        private
    {
        (ArbParams memory p, uint256 preBalance, address executor) = abi.decode(params, (ArbParams, uint256, address));
        if (p.asset != asset || p.amount != amount) revert CallbackAssetMismatch();

        _runRoute(p.steps, amount);

        uint256 balanceNow = DexRouter.balanceOf(asset, address(this));
        // Everything above the pre-loan balance is what the route generated.
        uint256 generated = balanceNow - preBalance;
        uint256 required = owed + p.minProfit;
        if (generated < required) revert InsufficientProfit(generated, required);

        uint256 profit = generated - owed;
        // Resolve the receiver once, and log THIS address rather than the raw
        // `p.profitReceiver`. On the default path (`profitReceiver == 0`, i.e.
        // "pay whoever signed the tx" — the connected-wallet default the
        // dashboard and the integration bots rely on) the raw field is the zero
        // address, so logging it would attribute every such trade's profit to
        // 0x0. PnL history for this engine lives in these logs, not in storage
        // (see the event's docstring), and the field is `indexed` — so a
        // downstream indexer filtering "arbitrage that paid me" by topic would
        // match nothing at all for exactly the path most operators use.
        address to = p.profitReceiver == address(0) ? executor : p.profitReceiver;
        if (profit != 0) {
            IERC20(asset).safeTransfer(to, profit);
        }

        emit ArbitrageExecuted(asset, provider, to, amount, owed, profit, p.steps.length);
    }

    /// @dev Feed-forward execution: the borrowed `amount` seeds hop 0, and each
    ///      hop's measured output seeds the next. Capping at the live balance
    ///      keeps the engine safe against dust and rounding.
    function _runRoute(SwapStep[] memory steps, uint256 amount) private {
        uint256 amountIn = amount;
        uint256 n = steps.length;
        for (uint256 i; i < n;) {
            SwapStep memory step = steps[i];
            // GENERIC hands the router fully attacker-controlled calldata (see
            // allowedGenericRouters' docstring) — gate it before it can run.
            // The typed dex types below don't need this: their call shape is
            // fixed to a well-known selector with the recipient hardcoded to
            // address(this), so they can't be repurposed to move an unrelated
            // held token the way an arbitrary GENERIC call could.
            if (step.dexType == DexType.GENERIC && !allowedGenericRouters[step.router]) {
                revert GenericRouterNotAllowed(step.router);
            }
            uint256 bal = DexRouter.balanceOf(step.tokenIn, address(this));
            if (amountIn > bal) amountIn = bal;
            if (amountIn == 0) revert ZeroAmount();
            amountIn = DexRouter.execute(step, amountIn, i);
            unchecked {
                ++i;
            }
        }
    }

    /// @dev Verifies a flash callback was solicited by this contract, then disarms.
    function _consumeArmed() private {
        if (_callbackState != _CB_ARMED) revert CallbackNotArmed();
        _callbackState = _CB_IDLE;
    }

    // --------------------------------------------------------------------- //
    //                       On-chain sizing helpers                         //
    // --------------------------------------------------------------------- //

    /// @notice Liquidity-aware optimal loan size for two-pool V2 arbitrage.
    /// @param pairBuy The V2 pair where the borrowed asset is spent for the intermediate token.
    /// @param pairSell The V2 pair where the intermediate token is sold back for the borrowed asset.
    /// @param tokenBorrow The asset to borrow and end in.
    /// @param feeBpsBuy Swap fee (bps) of the buy pool (e.g. 30 for a 0.30% pair).
    /// @param feeBpsSell Swap fee (bps) of the sell pool.
    /// @return amountIn The profit-maximising amount to borrow (0 if no profitable arb right now).
    /// @return expectedProfit The expected profit in `tokenBorrow` at that size.
    /// @dev View function — call it off-chain (or on-chain in a router) to size
    ///      the loan to current liquidity. The engine's atomic minProfit guard
    ///      remains the ultimate protection regardless of this estimate.
    function quoteOptimalTwoHopV2(
        address pairBuy,
        address pairSell,
        address tokenBorrow,
        uint256 feeBpsBuy,
        uint256 feeBpsSell
    ) external view returns (uint256 amountIn, uint256 expectedProfit) {
        (uint256 rInA, uint256 rOutA, address intermediate) = _pairInfo(pairBuy, tokenBorrow);
        (uint256 rInB, uint256 rOutB,) = _pairInfo(pairSell, intermediate);

        (amountIn,) = OptimalArbitrage.optimalV2Amount(rInA, rOutA, rInB, rOutB, feeBpsBuy);
        if (amountIn == 0) return (0, 0);

        uint256 out1 = OptimalArbitrage.getAmountOut(amountIn, rInA, rOutA, feeBpsBuy);
        uint256 out2 = OptimalArbitrage.getAmountOut(out1, rInB, rOutB, feeBpsSell);
        expectedProfit = out2 > amountIn ? out2 - amountIn : 0;
    }

    /// @notice The Aave flash-loan premium in bps (0 if Aave is not configured).
    /// @dev Raw staticcall in Yul, same idiom as `_getReserves`/`_token0`/
    ///      `_token1` below: a single-word external read with no meaningful
    ///      Solidity-side logic to keep. The selector comes from the imported
    ///      interface's `.selector` (a compile-time constant), not a typed
    ///      hex literal, so a wrong signature would be a compile error.
    function aavePremiumBps() external view returns (uint256 premium) {
        address pool = AAVE_POOL;
        if (pool == address(0)) return 0;
        bytes4 selector = IAaveV3Pool.FLASHLOAN_PREMIUM_TOTAL.selector;
        assembly {
            let ptr := mload(0x40)
            mstore(ptr, selector)
            let ok := staticcall(gas(), pool, ptr, 0x04, 0x00, 0x20)
            if or(iszero(ok), lt(returndatasize(), 0x20)) {
                returndatacopy(0x00, 0x00, returndatasize())
                revert(0x00, returndatasize())
            }
            premium := mload(0x00)
        }
    }

    /// @dev Returns (reserveOfToken, reserveOfOther, otherToken) for a V2 pair.
    ///      Reads `token0()` exactly once (the naive `_reservesFor` +
    ///      `_otherToken` split each re-read it, doubling that call) and
    ///      derives both the ordered reserves and the non-`token` address from
    ///      the single cached value.
    function _pairInfo(address pair, address token)
        private
        view
        returns (uint256 reserveToken, uint256 reserveOther, address otherToken)
    {
        (uint256 r0, uint256 r1) = _getReserves(pair);
        address t0 = _token0(pair);
        if (t0 == token) {
            return (r0, r1, _token1(pair));
        }
        return (r1, r0, t0);
    }

    /// @notice Gas-lean `getReserves()` read via a single staticcall in Yul
    ///         (same idiom as DexRouter.balanceOf). blockTimestampLast is
    ///         unused by the sizing math, so it's read but not returned.
    /// @dev The 3-word (0x60-byte) return doesn't fit the 0x00-0x3f scratch
    ///      space `balanceOf` uses for its single-word case, so input and
    ///      output both live at the free-memory pointer `ptr` instead — safe
    ///      because nothing below `ptr` is touched and the function returns
    ///      immediately after reading the words out, before any further
    ///      Solidity-level allocation.
    function _getReserves(address pair) private view returns (uint256 reserve0, uint256 reserve1) {
        assembly {
            let ptr := mload(0x40)
            // getReserves() selector = 0x0902f1ac
            mstore(ptr, 0x0902f1ac00000000000000000000000000000000000000000000000000000000)
            let ok := staticcall(gas(), pair, ptr, 0x04, ptr, 0x60)
            if or(iszero(ok), lt(returndatasize(), 0x60)) {
                returndatacopy(ptr, 0x00, returndatasize())
                revert(ptr, returndatasize())
            }
            reserve0 := mload(ptr)
            reserve1 := mload(add(ptr, 0x20))
        }
    }

    /// @notice Gas-lean `token0()` read via a single staticcall in Yul.
    function _token0(address pair) private view returns (address token) {
        assembly {
            let ptr := mload(0x40)
            // token0() selector = 0x0dfe1681
            mstore(ptr, 0x0dfe168100000000000000000000000000000000000000000000000000000000)
            let ok := staticcall(gas(), pair, ptr, 0x04, 0x00, 0x20)
            if or(iszero(ok), lt(returndatasize(), 0x20)) {
                returndatacopy(0x00, 0x00, returndatasize())
                revert(0x00, returndatasize())
            }
            token := mload(0x00)
        }
    }

    /// @notice Gas-lean `token1()` read via a single staticcall in Yul.
    function _token1(address pair) private view returns (address token) {
        assembly {
            let ptr := mload(0x40)
            // token1() selector = 0xd21220a7
            mstore(ptr, 0xd21220a700000000000000000000000000000000000000000000000000000000)
            let ok := staticcall(gas(), pair, ptr, 0x04, 0x00, 0x20)
            if or(iszero(ok), lt(returndatasize(), 0x20)) {
                returndatacopy(0x00, 0x00, returndatasize())
                revert(0x00, returndatasize())
            }
            token := mload(0x00)
        }
    }

    // --------------------------------------------------------------------- //
    //                          Admin / safety                               //
    // --------------------------------------------------------------------- //

    /// @notice Pauses new arbitrage executions.
    function pause() external onlyRole(GUARDIAN_ROLE) {
        _pause();
    }

    /// @notice Resumes arbitrage executions.
    function unpause() external onlyRole(GUARDIAN_ROLE) {
        _unpause();
    }

    /// @notice Allow or revoke a router address for GENERIC-type hops.
    /// @dev GUARDIAN_ROLE, not EXECUTOR_ROLE: the hot bot key that picks routes
    ///      each call must not also be able to expand what GENERIC is allowed
    ///      to call — a compromised EXECUTOR_ROLE key is limited to routes
    ///      through already-guardian-approved routers.
    function setGenericRouterAllowed(address router, bool allowed) external onlyRole(GUARDIAN_ROLE) {
        allowedGenericRouters[router] = allowed;
        emit GenericRouterAllowlistUpdated(router, allowed);
    }

    /// @notice Sweeps an ERC20 balance (e.g. accumulated profit) to `to`.
    /// @param token The token to sweep.
    /// @param to The recipient.
    /// @param amount The amount, or 0 to sweep the full balance.
    function rescueTokens(address token, address to, uint256 amount) external onlyRole(GUARDIAN_ROLE) {
        uint256 bal = IERC20(token).balanceOf(address(this));
        uint256 sweep = amount == 0 ? bal : amount;
        if (sweep == 0 || sweep > bal) revert NothingToRescue();
        IERC20(token).safeTransfer(to, sweep);
        emit Rescued(token, to, sweep);
    }

    /// @notice Sweeps native gas token (should normally be zero for this contract).
    function rescueETH(address payable to, uint256 amount) external onlyRole(GUARDIAN_ROLE) {
        uint256 bal = address(this).balance;
        uint256 sweep = amount == 0 ? bal : amount;
        if (sweep == 0 || sweep > bal) revert NothingToRescue();
        (bool ok,) = to.call{value: sweep}("");
        if (!ok) revert NothingToRescue();
        emit Rescued(address(0), to, sweep);
    }

    /// @notice Accept native token only from unwrapping helpers (kept minimal).
    receive() external payable {}
}
