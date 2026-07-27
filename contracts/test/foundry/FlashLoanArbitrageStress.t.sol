// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {Test, console2} from "forge-std/Test.sol";

import {FlashLoanArbitrage} from "../../contracts/FlashLoanArbitrage.sol";
import {ArbParams, SwapStep, FlashProvider, DexType} from "../../contracts/libraries/ArbTypes.sol";
import {IUniswapV2Router, IUniswapV2Pair} from "../../contracts/interfaces/dex/IUniswapV2.sol";
import {IBalancerVault} from "../../contracts/interfaces/IBalancerVault.sol";

interface IWETH {
    function deposit() external payable;
    function approve(address, uint256) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

interface ISushiFactory {
    function getPair(address, address) external view returns (address);
}

/// @title FlashLoanArbitrageStress
/// @notice Live-fork *stress + adversarial* suite for the flash-loan engine. The
///         sibling `FlashLoanArbitrageFork` proves the happy path executes; this
///         suite proves the safety envelope holds against REAL Arbitrum One
///         liquidity (Balancer V2 + Uniswap V3 + SushiSwap V2):
///           - the profit invariant reverts atomically when unreachable,
///           - the engine NEVER retains the borrowed asset at ANY borrow size
///             (bounded fuzz),
///           - every flash callback is unforgeable (unsolicited real Balancer
///             loan + direct callbacks are rejected),
///           - access-control, pause and deadline gates hold.
/// @dev Run with:
///        forge test --match-path 'test/foundry/*' --fork-url "$ARBITRUM_RPC_URL" -vv
///      Self-skips cleanly (offline) so the default `forge test` stays green.
contract FlashLoanArbitrageStress is Test {
    address constant BALANCER_VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address constant AAVE_POOL = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address constant USDCE = 0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8;
    address constant UNIV3_ROUTER02 = 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;
    address constant SUSHI_ROUTER = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;
    address constant SUSHI_FACTORY = 0xc35DADB65012eC5796536bD9864eD8773aBc74C4;

    uint256 internal constant MIN_PROFIT = 0.001 ether;

    FlashLoanArbitrage internal arb;
    address internal receiver = makeAddr("receiver");
    address internal attacker = makeAddr("attacker");

    function setUp() public {
        // Only meaningful on an Arbitrum fork; skip cleanly otherwise.
        if (WETH.code.length == 0) return;
        arb = new FlashLoanArbitrage(AAVE_POOL, BALANCER_VAULT, address(this));
    }

    function _isArbitrumFork() internal view returns (bool) {
        return WETH.code.length != 0 && BALANCER_VAULT.code.length != 0;
    }

    /// @dev The canonical capture route: sell WETH high on Uni V3, buy it back
    ///      cheap on the (dislocated) Sushi pool.
    function _captureSteps() internal pure returns (SwapStep[] memory steps) {
        steps = new SwapStep[](2);
        steps[0] = SwapStep({
            dexType: DexType.UNISWAP_V3_SINGLE,
            router: UNIV3_ROUTER02,
            tokenIn: WETH,
            tokenOut: USDCE,
            poolFee: 500,
            curveI: 0,
            curveJ: 0,
            minOut: 0,
            data: "",
            amountInOffset: 0
        });
        steps[1] = SwapStep({
            dexType: DexType.UNISWAP_V2,
            router: SUSHI_ROUTER,
            tokenIn: USDCE,
            tokenOut: WETH,
            poolFee: 0,
            curveI: 0,
            curveJ: 0,
            minOut: 0,
            data: "",
            amountInOffset: 0
        });
    }

    /// @dev Manufactures a live price dislocation by dumping ~50% of the Sushi
    ///      pool's WETH into it, and returns the pool's pre-dump WETH reserve
    ///      (used to size borrows as a fraction of real liquidity).
    function _dislocateSushi() internal returns (uint256 wethReserve) {
        address pair = ISushiFactory(SUSHI_FACTORY).getPair(WETH, USDCE);
        (uint112 r0, uint112 r1,) = IUniswapV2Pair(pair).getReserves();
        wethReserve = IUniswapV2Pair(pair).token0() == WETH ? r0 : r1;

        uint256 dump = (wethReserve * 50) / 100;
        vm.deal(address(this), dump + 1 ether);
        IWETH(WETH).deposit{value: dump + 1 ether}();
        IWETH(WETH).approve(SUSHI_ROUTER, type(uint256).max);
        address[] memory path = new address[](2);
        path[0] = WETH;
        path[1] = USDCE;
        IUniswapV2Router(SUSHI_ROUTER).swapExactTokensForTokens(dump, 0, path, address(this), block.timestamp);
    }

    function _params(FlashProvider provider, uint256 amount, uint256 minProfit, uint256 deadline)
        internal
        view
        returns (ArbParams memory)
    {
        return ArbParams({
            provider: provider,
            asset: WETH,
            amount: amount,
            minProfit: minProfit,
            profitReceiver: receiver,
            deadline: deadline,
            steps: _captureSteps()
        });
    }

    // ---------------------------------------------------------------------- //
    //   Profit invariant under real liquidity                                //
    // ---------------------------------------------------------------------- //

    /// The atomic min-profit guard must fire under live liquidity: an
    /// unreachable `minProfit` reverts the whole trade and leaves nothing behind.
    function testForkProfitInvariantRevertsWhenUnreachable() public {
        if (!_isArbitrumFork()) return;
        uint256 wethReserve = _dislocateSushi();
        uint256 borrow = (wethReserve * 2) / 100;

        // Demand a preposterous profit the manufactured gap cannot deliver.
        ArbParams memory p = _params(FlashProvider.BALANCER_V2, borrow, 10_000 ether, type(uint256).max);
        vm.expectRevert(); // InsufficientProfit — the whole flash loan unwinds
        arb.executeArbitrage(p);

        assertEq(IWETH(WETH).balanceOf(address(arb)), 0, "engine retained the borrowed asset after revert");
    }

    /// STRESS: for ANY borrow size (0.1%..8% of the live Sushi reserve) the
    /// engine either captures a profit or reverts — but it must NEVER end
    /// holding the borrowed asset. This is the profit-safety invariant.
    /// forge-config: default.fuzz.runs = 12
    function testForkFuzzEngineNeverRetainsBorrowedAsset(uint256 pctBps) public {
        if (!_isArbitrumFork()) return;
        pctBps = bound(pctBps, 10, 800); // 0.10% .. 8.00% of the reserve
        uint256 wethReserve = _dislocateSushi();
        uint256 borrow = (wethReserve * pctBps) / 10_000;
        vm.assume(borrow > 0);

        // minProfit = 0: accept any non-negative outcome. Whether the trade
        // executes (profit forwarded to `receiver`) or reverts atomically, the
        // engine must end holding none of the borrowed asset.
        ArbParams memory p = _params(FlashProvider.BALANCER_V2, borrow, 0, type(uint256).max);
        try arb.executeArbitrage(p) {
            assertEq(IWETH(WETH).balanceOf(address(arb)), 0, "engine retained the borrowed asset (executed)");
        } catch {
            assertEq(IWETH(WETH).balanceOf(address(arb)), 0, "engine retained the borrowed asset (reverted)");
        }
    }

    // ---------------------------------------------------------------------- //
    //   Callback forgery resistance against the REAL Balancer Vault          //
    // ---------------------------------------------------------------------- //

    /// An attacker who asks the REAL Balancer Vault to flash-loan straight into
    /// our contract must be rejected: the armed-latch was never set, so the
    /// callback reverts and the whole (griefing) loan unwinds.
    function testForkRejectsUnsolicitedRealBalancerFlashLoan() public {
        if (!_isArbitrumFork()) return;
        address[] memory tokens = new address[](1);
        uint256[] memory amounts = new uint256[](1);
        tokens[0] = WETH;
        amounts[0] = 1 ether;

        vm.prank(attacker);
        vm.expectRevert(); // CallbackNotArmed (bubbled through the Vault)
        IBalancerVault(BALANCER_VAULT).flashLoan(address(arb), tokens, amounts, "");
    }

    /// Direct calls to either flash callback are rejected with UnexpectedCaller,
    /// because neither the real Aave Pool nor the real Balancer Vault is msg.sender.
    function testForkRejectsDirectCallbacks() public {
        if (!_isArbitrumFork()) return;
        address[] memory tokens = new address[](1);
        uint256[] memory amounts = new uint256[](1);
        uint256[] memory fees = new uint256[](1);
        tokens[0] = WETH;
        amounts[0] = 1 ether;

        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(FlashLoanArbitrage.UnexpectedCaller.selector, attacker));
        arb.receiveFlashLoan(tokens, amounts, fees, "");

        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(FlashLoanArbitrage.UnexpectedCaller.selector, attacker));
        arb.executeOperation(WETH, 1 ether, 0, address(arb), "");
    }

    // ---------------------------------------------------------------------- //
    //   Access control, pause and deadline gates                             //
    // ---------------------------------------------------------------------- //

    /// Only EXECUTOR_ROLE may start an arbitrage, even on live liquidity.
    function testForkEnforcesExecutorRole() public {
        if (!_isArbitrumFork()) return;
        ArbParams memory p = _params(FlashProvider.BALANCER_V2, 1 ether, MIN_PROFIT, type(uint256).max);
        vm.prank(attacker);
        vm.expectRevert(); // AccessControlUnauthorizedAccount
        arb.executeArbitrage(p);
    }

    /// A paused engine refuses execution.
    function testForkPauseHalts() public {
        if (!_isArbitrumFork()) return;
        arb.pause();
        ArbParams memory p = _params(FlashProvider.BALANCER_V2, 1 ether, MIN_PROFIT, type(uint256).max);
        vm.expectRevert(); // EnforcedPause
        arb.executeArbitrage(p);
    }

    /// An expired deadline reverts before any borrow happens.
    function testForkRevertsOnExpiredDeadline() public {
        if (!_isArbitrumFork()) return;
        ArbParams memory p = _params(FlashProvider.BALANCER_V2, 1 ether, MIN_PROFIT, block.timestamp - 1);
        vm.expectRevert(FlashLoanArbitrage.DeadlineExpired.selector);
        arb.executeArbitrage(p);
    }

    // ---------------------------------------------------------------------- //
    //   Live sizing helper sanity                                            //
    // ---------------------------------------------------------------------- //

    /// The on-chain optimal-size quoter must read live reserves without
    /// reverting and never propose a loss-making trade.
    function testForkOptimalQuoterReadsLiveReservesSafely() public view {
        if (!_isArbitrumFork()) return;
        address pair = ISushiFactory(SUSHI_FACTORY).getPair(WETH, USDCE);
        // Same pool on both legs => a round trip can only lose fees, so the
        // quoter must return (0, 0) rather than a phantom-profitable size.
        (uint256 amountIn, uint256 expectedProfit) = arb.quoteOptimalTwoHopV2(pair, pair, WETH, 30, 30);
        assertEq(amountIn, 0, "quoter proposed a size on a non-arbitrageable single pool");
        assertEq(expectedProfit, 0, "quoter fabricated profit");
    }
}
