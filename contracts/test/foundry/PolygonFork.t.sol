// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {Test, console2} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {FlashLoanArbitrage} from "../../contracts/FlashLoanArbitrage.sol";
import {ArbParams, SwapStep, FlashProvider, DexType} from "../../contracts/libraries/ArbTypes.sol";
import {IUniswapV2Router, IUniswapV2Pair} from "../../contracts/interfaces/dex/IUniswapV2.sol";

interface IWMATIC {
    function deposit() external payable;
    function approve(address, uint256) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

interface IUniswapV2RouterFactory {
    function factory() external view returns (address);
}

interface IUniswapV2Factory {
    function getPair(address, address) external view returns (address);
}

/// @title PolygonFork
/// @notice Foundry mirror of test/fork/PolygonFork.test.js: a real atomic
///         cross-DEX flash-loan arbitrage against live Aave V3 / Balancer V2,
///         Uniswap V3 SwapRouter02, and QuickSwap V2 on a Polygon PoS fork.
/// @dev Run with:
///        forge test --match-path 'test/foundry/PolygonFork.t.sol' --fork-url "$POLYGON_RPC_URL" -vvv
///      Addresses, sizing (50% manufactured dump, 0.1% borrow), and the
///      WMATIC-not-WETH choice were all validated empirically against a live
///      Polygon fork via the equivalent Hardhat suite before writing this
///      file (see docs/notes-cross-chain-flash-loans.md) — this session could
///      not run `forge` directly (GitHub is blocked by this sandbox's egress
///      policy), so this file mirrors already-proven parameters rather than
///      fresh guesses, but has not itself been executed here. Run it in CI or
///      locally to get a live Foundry-side confirmation.
///
///      Uses WMATIC, not WETH: Polygon's native gas token is MATIC, so WMATIC
///      (not the bridged, non-wrappable WETH at 0x7ceB23fD…) is the canonical
///      wrapped-native-token contract with a `deposit()` function.
///
///      QuickSwap's factory is read live via the router's own `factory()`
///      call rather than hardcoded, avoiding a second guessed address.
contract PolygonFork is Test {
    address constant AAVE_POOL = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant BALANCER_VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address constant WMATIC = 0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270;
    address constant USDCe = 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174;
    address constant UNIV3_ROUTER02 = 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;
    address constant QUICKSWAP_ROUTER = 0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff;

    FlashLoanArbitrage internal arb;
    address internal receiver = makeAddr("receiver");

    function setUp() public {
        // Only meaningful on a Polygon fork; skip cleanly otherwise.
        if (WMATIC.code.length == 0) return;
        arb = new FlashLoanArbitrage(AAVE_POOL, BALANCER_VAULT, address(this));
    }

    function _isPolygonFork() internal view returns (bool) {
        return WMATIC.code.length != 0 && BALANCER_VAULT.code.length != 0;
    }

    uint256 internal constant MIN_PROFIT = 1 ether; // 1 WMATIC

    /// @dev Manufactures a live price dislocation on QuickSwap (dumps 50% of
    ///      its WMATIC reserve), then borrows via `provider` and captures it
    ///      across Uni V3 -> QuickSwap V2. Sized to 0.1% of QuickSwap's
    ///      reserve: the Uniswap V3 0.3%-fee-tier pool for this pair proved
    ///      (empirically, via the Hardhat mirror) to carry much thinner
    ///      concentrated liquidity than QuickSwap's raw reserves, so a larger
    ///      (e.g. 2%) borrow eats itself alive in V3-side slippage before it
    ///      ever reaches InsufficientProfit's real signal.
    function _runManufacturedArb(FlashProvider provider) internal returns (uint256 profit) {
        address factory = IUniswapV2RouterFactory(QUICKSWAP_ROUTER).factory();
        address pair = IUniswapV2Factory(factory).getPair(WMATIC, USDCe);
        (uint112 r0, uint112 r1,) = IUniswapV2Pair(pair).getReserves();
        uint256 wmaticReserve = IUniswapV2Pair(pair).token0() == WMATIC ? r0 : r1;

        // 1) Manufacture a dislocation: dump ~50% of the pool's WMATIC into QuickSwap.
        uint256 dump = (wmaticReserve * 50) / 100;
        uint256 wrapAmount = dump + 1000 ether;
        vm.deal(address(this), wrapAmount + 1000 ether); // extra headroom for gas, on top of the wrapped value
        IWMATIC(WMATIC).deposit{value: wrapAmount}();
        IWMATIC(WMATIC).approve(QUICKSWAP_ROUTER, type(uint256).max);
        address[] memory path = new address[](2);
        path[0] = WMATIC;
        path[1] = USDCe;
        IUniswapV2Router(QUICKSWAP_ROUTER).swapExactTokensForTokens(dump, 0, path, address(this), block.timestamp);

        // 2) Capture it: borrow a modest slice, sell high on Uni V3, buy back cheap on QuickSwap.
        uint256 borrow = (wmaticReserve * 1) / 1000;
        SwapStep[] memory steps = new SwapStep[](2);
        steps[0] = SwapStep({
            dexType: DexType.UNISWAP_V3_SINGLE,
            router: UNIV3_ROUTER02,
            tokenIn: WMATIC,
            tokenOut: USDCe,
            poolFee: 3000,
            curveI: 0,
            curveJ: 0,
            minOut: 0,
            data: "",
            amountInOffset: 0
        });
        steps[1] = SwapStep({
            dexType: DexType.UNISWAP_V2,
            router: QUICKSWAP_ROUTER,
            tokenIn: USDCe,
            tokenOut: WMATIC,
            poolFee: 0,
            curveI: 0,
            curveJ: 0,
            minOut: 0,
            data: "",
            amountInOffset: 0
        });

        ArbParams memory p = ArbParams({
            provider: provider,
            asset: WMATIC,
            amount: borrow,
            minProfit: MIN_PROFIT,
            profitReceiver: receiver,
            deadline: type(uint256).max,
            steps: steps
        });

        uint256 before = IWMATIC(WMATIC).balanceOf(receiver);
        arb.executeArbitrage(p);
        profit = IWMATIC(WMATIC).balanceOf(receiver) - before;
        assertEq(IWMATIC(WMATIC).balanceOf(address(arb)), 0, "engine retained the borrowed asset");
    }

    /// Balancer V2 flash loan (0-fee) — the atomic borrow -> cross-DEX -> repay path.
    function testForkRealCrossDexArbitrage() public {
        if (!_isPolygonFork()) {
            emit log("skipped: not a Polygon fork (set POLYGON_RPC_URL)");
            return;
        }
        uint256 profit = _runManufacturedArb(FlashProvider.BALANCER_V2);
        console2.log("Balancer captured profit (wei):", profit);
        assertGt(profit, MIN_PROFIT, "arb did not clear min profit");
    }

    /// Aave V3 flash loan — same path, borrowing from the live Aave Pool.
    function testForkRealCrossDexArbitrageViaAave() public {
        if (!_isPolygonFork()) return;
        uint256 profit = _runManufacturedArb(FlashProvider.AAVE_V3);
        console2.log("Aave captured profit (wei):", profit);
        assertGt(profit, MIN_PROFIT, "aave arb did not clear min profit");
    }

    function testForkReadsLiveAavePremium() public view {
        if (!_isPolygonFork()) return;
        assertGt(arb.aavePremiumBps(), 0, "expected a live Aave premium");
    }
}
