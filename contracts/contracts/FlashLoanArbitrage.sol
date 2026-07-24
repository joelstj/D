// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {ArbParams, SwapStep, FlashProvider} from "./libraries/ArbTypes.sol";
import {DexRouter} from "./libraries/DexRouter.sol";
import {OptimalArbitrage} from "./libraries/OptimalArbitrage.sol";
import {IAaveV3Pool, IAaveFlashLoanSimpleReceiver} from "./interfaces/IAaveV3Pool.sol";
import {IBalancerVault, IBalancerFlashLoanRecipient} from "./interfaces/IBalancerVault.sol";
import {IUniswapV2Pair} from "./interfaces/dex/IUniswapV2.sol";

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
    //                               Errors                                  //
    // --------------------------------------------------------------------- //

    error ProviderNotConfigured();
    error InvalidProvider();
    error DeadlineExpired();
    error InvalidRoute();
    error RouteAssetMismatch();
    error ZeroAmount();
    error UnexpectedCaller(address caller);
    error UnexpectedInitiator(address initiator);
    error CallbackNotArmed();
    error CallbackAssetMismatch();
    error InsufficientProfit(uint256 generated, uint256 required);
    error NothingToRescue();

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
    function executeArbitrage(ArbParams calldata p)
        external
        nonReentrant
        whenNotPaused
        onlyRole(EXECUTOR_ROLE)
    {
        if (block.timestamp > p.deadline) revert DeadlineExpired();
        uint256 nSteps = p.steps.length;
        if (nSteps < 2) revert InvalidRoute();
        if (p.amount == 0) revert ZeroAmount();
        if (p.steps[0].tokenIn != p.asset || p.steps[nSteps - 1].tokenOut != p.asset) {
            revert RouteAssetMismatch();
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
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
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
    function _settle(
        address asset,
        uint256 amount,
        uint256 owed,
        bytes calldata params,
        FlashProvider provider
    ) private {
        (ArbParams memory p, uint256 preBalance, address executor) =
            abi.decode(params, (ArbParams, uint256, address));
        if (p.asset != asset || p.amount != amount) revert CallbackAssetMismatch();

        _runRoute(p.steps, amount);

        uint256 balanceNow = DexRouter.balanceOf(asset, address(this));
        // Everything above the pre-loan balance is what the route generated.
        uint256 generated = balanceNow - preBalance;
        uint256 required = owed + p.minProfit;
        if (generated < required) revert InsufficientProfit(generated, required);

        uint256 profit = generated - owed;
        if (profit != 0) {
            address to = p.profitReceiver == address(0) ? executor : p.profitReceiver;
            IERC20(asset).safeTransfer(to, profit);
        }

        emit ArbitrageExecuted(
            asset, provider, p.profitReceiver, amount, owed, profit, p.steps.length
        );
    }

    /// @dev Feed-forward execution: the borrowed `amount` seeds hop 0, and each
    ///      hop's measured output seeds the next. Capping at the live balance
    ///      keeps the engine safe against dust and rounding.
    function _runRoute(SwapStep[] memory steps, uint256 amount) private {
        uint256 amountIn = amount;
        uint256 n = steps.length;
        for (uint256 i; i < n;) {
            SwapStep memory step = steps[i];
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
        (uint256 rInA, uint256 rOutA) = _reservesFor(pairBuy, tokenBorrow);
        address intermediate = _otherToken(pairBuy, tokenBorrow);
        (uint256 rInB, uint256 rOutB) = _reservesFor(pairSell, intermediate);

        (amountIn,) = OptimalArbitrage.optimalV2Amount(rInA, rOutA, rInB, rOutB, feeBpsBuy);
        if (amountIn == 0) return (0, 0);

        uint256 out1 = OptimalArbitrage.getAmountOut(amountIn, rInA, rOutA, feeBpsBuy);
        uint256 out2 = OptimalArbitrage.getAmountOut(out1, rInB, rOutB, feeBpsSell);
        expectedProfit = out2 > amountIn ? out2 - amountIn : 0;
    }

    /// @notice The Aave flash-loan premium in bps (0 if Aave is not configured).
    function aavePremiumBps() external view returns (uint256) {
        if (AAVE_POOL == address(0)) return 0;
        return uint256(IAaveV3Pool(AAVE_POOL).FLASHLOAN_PREMIUM_TOTAL());
    }

    /// @dev Returns (reserveOfToken, reserveOfOther) for a V2 pair.
    function _reservesFor(address pair, address token)
        private
        view
        returns (uint256 reserveToken, uint256 reserveOther)
    {
        (uint112 r0, uint112 r1,) = IUniswapV2Pair(pair).getReserves();
        if (IUniswapV2Pair(pair).token0() == token) {
            return (uint256(r0), uint256(r1));
        }
        return (uint256(r1), uint256(r0));
    }

    /// @dev Returns whichever of a pair's tokens is not `token`.
    function _otherToken(address pair, address token) private view returns (address) {
        address t0 = IUniswapV2Pair(pair).token0();
        return t0 == token ? IUniswapV2Pair(pair).token1() : t0;
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

    /// @notice Sweeps an ERC20 balance (e.g. accumulated profit) to `to`.
    /// @param token The token to sweep.
    /// @param to The recipient.
    /// @param amount The amount, or 0 to sweep the full balance.
    function rescueTokens(address token, address to, uint256 amount)
        external
        onlyRole(GUARDIAN_ROLE)
    {
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
