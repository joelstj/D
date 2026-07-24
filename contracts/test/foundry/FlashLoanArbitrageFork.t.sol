// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {Test, console2} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {FlashLoanArbitrage} from "../../contracts/FlashLoanArbitrage.sol";
import {ArbParams, SwapStep, FlashProvider, DexType} from "../../contracts/libraries/ArbTypes.sol";
import {IUniswapV2Router, IUniswapV2Pair} from "../../contracts/interfaces/dex/IUniswapV2.sol";

interface IWETH {
    function deposit() external payable;
    function approve(address, uint256) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

interface ISushiFactory {
    function getPair(address, address) external view returns (address);
}

/// @title FlashLoanArbitrageFork
/// @notice Foundry mirror of the Hardhat live-fork suite: a real atomic
///         cross-DEX flash-loan arbitrage against live Balancer V2 + Uniswap V3
///         + SushiSwap V2 on an Arbitrum One fork.
/// @dev Run with:
///        forge test --match-path 'test/foundry/*' --fork-url "$ARBITRUM_RPC_URL" -vvv
///      Uses Balancer as the flash provider (Aave V3's proxy/library path does
///      not execute inside a public-RPC fork — see docs/DEPLOYMENT.md).
contract FlashLoanArbitrageFork is Test {
    address constant BALANCER_VAULT = 0xBA12222222228d8Ba445958a75a0704d566BF2C8;
    address constant AAVE_POOL = 0x794a61358D6845594F94dc1DB02A252b5b4814aD;
    address constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address constant USDCe = 0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8;
    address constant UNIV3_ROUTER02 = 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;
    address constant SUSHI_ROUTER = 0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506;
    address constant SUSHI_FACTORY = 0xc35DADB65012eC5796536bD9864eD8773aBc74C4;

    FlashLoanArbitrage internal arb;
    address internal receiver = makeAddr("receiver");

    function setUp() public {
        // Only meaningful on an Arbitrum fork; skip cleanly otherwise.
        if (WETH.code.length == 0) return;
        arb = new FlashLoanArbitrage(AAVE_POOL, BALANCER_VAULT, address(this));
    }

    function _isArbitrumFork() internal view returns (bool) {
        return WETH.code.length != 0 && BALANCER_VAULT.code.length != 0;
    }

    function testForkRealCrossDexArbitrage() public {
        if (!_isArbitrumFork()) {
            emit log("skipped: not an Arbitrum fork (pass --fork-url $ARBITRUM_RPC_URL)");
            return;
        }

        // Live Sushi WETH reserve drives sizing.
        address pair = ISushiFactory(SUSHI_FACTORY).getPair(WETH, USDCe);
        (uint112 r0, uint112 r1,) = IUniswapV2Pair(pair).getReserves();
        uint256 wethReserve = IUniswapV2Pair(pair).token0() == WETH ? r0 : r1;

        // 1) Manufacture a dislocation: dump ~50% of the pool's WETH into Sushi.
        uint256 dump = (wethReserve * 50) / 100;
        vm.deal(address(this), dump + 1 ether);
        IWETH(WETH).deposit{value: dump + 1 ether}();
        IWETH(WETH).approve(SUSHI_ROUTER, type(uint256).max);
        address[] memory path = new address[](2);
        path[0] = WETH;
        path[1] = USDCe;
        IUniswapV2Router(SUSHI_ROUTER).swapExactTokensForTokens(
            dump, 0, path, address(this), block.timestamp
        );

        // 2) Capture it via a Balancer flash loan across Uni V3 then Sushi V2.
        uint256 borrow = (wethReserve * 2) / 100;
        uint256 minProfit = 0.001 ether;

        SwapStep[] memory steps = new SwapStep[](2);
        steps[0] = SwapStep({
            dexType: DexType.UNISWAP_V3_SINGLE,
            router: UNIV3_ROUTER02,
            tokenIn: WETH,
            tokenOut: USDCe,
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
            tokenIn: USDCe,
            tokenOut: WETH,
            poolFee: 0,
            curveI: 0,
            curveJ: 0,
            minOut: 0,
            data: "",
            amountInOffset: 0
        });

        ArbParams memory p = ArbParams({
            provider: FlashProvider.BALANCER_V2,
            asset: WETH,
            amount: borrow,
            minProfit: minProfit,
            profitReceiver: receiver,
            deadline: type(uint256).max,
            steps: steps
        });

        uint256 before = IWETH(WETH).balanceOf(receiver);
        arb.executeArbitrage(p);
        uint256 profit = IWETH(WETH).balanceOf(receiver) - before;

        console2.log("captured profit (wei):", profit);
        assertGt(profit, minProfit, "arb did not clear min profit");
        assertEq(IWETH(WETH).balanceOf(address(arb)), 0, "engine retained the borrowed asset");
    }

    function testForkReadsLiveAavePremium() public view {
        if (!_isArbitrumFork()) return;
        assertGt(arb.aavePremiumBps(), 0, "expected a live Aave premium");
    }
}
