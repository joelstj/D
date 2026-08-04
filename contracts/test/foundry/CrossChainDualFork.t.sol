// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {Test, console2} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {CrossChainArbitrageExecutor} from "../../contracts/crosschain/CrossChainArbitrageExecutor.sol";
import {MockBridgeAdapter} from "../../contracts/mocks/TestHelpers.sol";
import {SwapStep, DexType} from "../../contracts/libraries/ArbTypes.sol";

interface IWrappedNative {
    function deposit() external payable;
    function transfer(address, uint256) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

interface IUniswapV2RouterFactory {
    function factory() external view returns (address);
}

interface IUniswapV2Factory {
    function getPair(address, address) external view returns (address);
}

/// @title CrossChainDualFork
/// @notice Foundry mirror of test/fork/CrossChainDualFork.test.js: proves the
///         inventory-based, two-transaction cross-chain model end-to-end
///         across TWO simultaneously-held live forks (Polygon + Arbitrum One)
///         using Foundry's native `vm.createFork`/`vm.selectFork` — the more
///         idiomatic Foundry equivalent of the Hardhat version's
///         `hardhat_reset`-based chain-switching.
/// @dev Run with:
///        POLYGON_RPC_URL=... ARBITRUM_RPC_URL=... \
///        forge test --match-path 'test/foundry/CrossChainDualFork.t.sol' -vvv
///      (no single --fork-url: this test creates both forks itself from env
///      vars, and needs an unforked default so vm.createFork can run twice).
///
///      READ FIRST: an atomic cross-chain flash loan does not exist — a
///      single transaction cannot span two chains. This proves the real
///      production model instead: CrossChainArbitrageExecutor's
///      inventory-based source/destination legs, each executed for real
///      against live state on its own chain. The ONLY simulated step is the
///      bridge/relayer itself (no real relayer can act on an ephemeral local
///      fork) — see the file-level comment in the Hardhat mirror for the
///      full rationale; the same simplification (1:1 delivery via each
///      chain's own wrapped-native-token `deposit()`) is used here.
///
///      Parameters (seed amount, token choice) match the Hardhat version,
///      which was actually run against live Polygon + Arbitrum state before
///      this file was written (see docs/notes-cross-chain-flash-loans.md) —
///      this file itself was not executed in this sandbox (GitHub, and so
///      `forge`, is blocked by this session's egress policy).
contract CrossChainDualFork is Test {
    address constant POLYGON_WMATIC = 0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270;
    address constant POLYGON_WETH = 0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619;
    address constant POLYGON_QUICKSWAP_ROUTER = 0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff;

    address constant ARBITRUM_WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address constant ARBITRUM_USDCe = 0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8;
    address constant ARBITRUM_UNIV3_ROUTER02 = 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;

    function testCrossChainSourceThenDestinationLeg() public {
        string memory polygonRpc = vm.envOr("POLYGON_RPC_URL", string(""));
        string memory arbitrumRpc = vm.envOr("ARBITRUM_RPC_URL", string(""));
        if (bytes(polygonRpc).length == 0 || bytes(arbitrumRpc).length == 0) {
            emit log("skipped: set POLYGON_RPC_URL and ARBITRUM_RPC_URL to run");
            return;
        }

        uint256 polygonFork = vm.createFork(polygonRpc);
        uint256 arbitrumFork = vm.createFork(arbitrumRpc);

        // ---------------------------------------------------------------
        // Leg 1 — Polygon (source chain): swap real WMATIC inventory into
        // WETH via a real QuickSwap pool, dispatch it to a bridge adapter.
        // ---------------------------------------------------------------
        vm.selectFork(polygonFork);
        require(POLYGON_WMATIC.code.length != 0, "not a Polygon fork");

        address admin = makeAddr("admin");
        address bot = makeAddr("bot");
        address manipulator = makeAddr("manipulator");

        CrossChainArbitrageExecutor sourceExec = new CrossChainArbitrageExecutor(admin);
        vm.prank(admin);
        sourceExec.grantRole(sourceExec.EXECUTOR_ROLE(), bot);

        MockBridgeAdapter bridge = new MockBridgeAdapter();
        // Deny-by-default: executeSourceLeg only accepts an allowlisted
        // bridge adapter (see CrossChainArbitrageExecutor.sol's
        // allowedBridgeAdapters / the Hardhat mirror of this fix).
        vm.prank(admin);
        sourceExec.setBridgeAdapterAllowed(address(bridge), true);

        uint256 seedAmount = 1000 ether; // 1000 WMATIC of inventory
        vm.deal(manipulator, seedAmount + 10 ether);
        vm.prank(manipulator);
        IWrappedNative(POLYGON_WMATIC).deposit{value: seedAmount}();
        vm.prank(manipulator);
        IWrappedNative(POLYGON_WMATIC).transfer(address(sourceExec), seedAmount);

        address quickswapFactory = IUniswapV2RouterFactory(POLYGON_QUICKSWAP_ROUTER).factory();
        address wmaticWethPair = IUniswapV2Factory(quickswapFactory).getPair(POLYGON_WMATIC, POLYGON_WETH);
        require(wmaticWethPair != address(0), "no live QuickSwap WMATIC/WETH pair found");

        SwapStep[] memory sourceSteps = new SwapStep[](1);
        sourceSteps[0] = SwapStep({
            dexType: DexType.UNISWAP_V2,
            router: POLYGON_QUICKSWAP_ROUTER,
            tokenIn: POLYGON_WMATIC,
            tokenOut: POLYGON_WETH,
            poolFee: 0,
            curveI: 0,
            curveJ: 0,
            minOut: 0,
            data: "",
            amountInOffset: 0
        });

        vm.prank(bot);
        sourceExec.executeSourceLeg(
            sourceSteps,
            POLYGON_WMATIC,
            seedAmount,
            address(bridge),
            POLYGON_WETH,
            1, // minBridgeAmount - just a nonzero floor, this test isn't about sizing
            42161, // dstChainId (Arbitrum One) - recorded in the event only
            bot, // dstRecipient - informational for a real bridge; unused by the mock
            ""
        );

        uint256 bridgedWeth = IERC20(POLYGON_WETH).balanceOf(address(bridge));
        assertGt(bridgedWeth, 0, "source leg produced nothing to bridge");
        assertEq(IERC20(POLYGON_WETH).balanceOf(address(sourceExec)), 0, "source executor retained WETH");
        console2.log("Polygon source leg bridged WETH (wei):", bridgedWeth);

        // ---------------------------------------------------------------
        // Leg 2 — Arbitrum (destination chain): the bridge/relayer step is
        // the one deliberately simulated part (see contract-level notice
        // above) — deliver the same amount of value as real Arbitrum WETH
        // via deposit(), then run the destination leg against a real
        // Uniswap V3 pool.
        // ---------------------------------------------------------------
        vm.selectFork(arbitrumFork);
        require(ARBITRUM_WETH.code.length != 0, "not an Arbitrum One fork");

        CrossChainArbitrageExecutor destExec = new CrossChainArbitrageExecutor(admin);
        vm.prank(admin);
        destExec.grantRole(destExec.EXECUTOR_ROLE(), bot);

        vm.deal(manipulator, bridgedWeth + 1 ether);
        vm.prank(manipulator);
        IWrappedNative(ARBITRUM_WETH).deposit{value: bridgedWeth}();
        vm.prank(manipulator);
        IWrappedNative(ARBITRUM_WETH).transfer(address(destExec), bridgedWeth);

        SwapStep[] memory destSteps = new SwapStep[](1);
        destSteps[0] = SwapStep({
            dexType: DexType.UNISWAP_V3_SINGLE,
            router: ARBITRUM_UNIV3_ROUTER02,
            tokenIn: ARBITRUM_WETH,
            tokenOut: ARBITRUM_USDCe,
            poolFee: 500,
            curveI: 0,
            curveJ: 0,
            minOut: 0,
            data: "",
            amountInOffset: 0
        });

        vm.prank(bot);
        destExec.executeDestinationLeg(destSteps, ARBITRUM_WETH, 0, 1);

        uint256 finalUsdc = IERC20(ARBITRUM_USDCe).balanceOf(address(destExec));
        assertGt(finalUsdc, 0, "destination leg produced no USDC.e");
        console2.log("Arbitrum destination leg settled USDC.e (6dp):", finalUsdc);
    }

    // ---------------------------------------------------------------
    // New coverage for the bridge-adapter allowlist and sibling-executor
    // registry (docs/notes-cross-chain-flash-loan-gaps.md items C2/C6).
    // Mirrors the equivalent cases added to
    // test/CrossChainArbitrageExecutor.test.js and the allowlist fix
    // applied above to testCrossChainSourceThenDestinationLeg.
    //
    // WRITTEN, NOT EXECUTED IN THIS SANDBOX — forge is unavailable here
    // (see the file-level notice above); unlike the Hardhat mirror this
    // file otherwise tracks, this function has not been run against a
    // real fork by any session to date.
    //
    // Only forks Polygon (the source chain): both new checks are entirely
    // a source-leg concern that reverts (when they revert at all) before
    // any destination-chain interaction, so a second (Arbitrum) fork would
    // buy nothing here.
    // ---------------------------------------------------------------
    function testExecuteSourceLegBridgeAdapterAndSiblingGuardrails() public {
        string memory polygonRpc = vm.envOr("POLYGON_RPC_URL", string(""));
        if (bytes(polygonRpc).length == 0) {
            emit log("skipped: set POLYGON_RPC_URL to run");
            return;
        }
        vm.selectFork(vm.createFork(polygonRpc));
        require(POLYGON_WMATIC.code.length != 0, "not a Polygon fork");

        address admin = makeAddr("admin2");
        address bot = makeAddr("bot2");
        address sibling = makeAddr("sibling");
        address wrongRecipient = makeAddr("wrongRecipient");

        CrossChainArbitrageExecutor exec = new CrossChainArbitrageExecutor(admin);
        vm.prank(admin);
        exec.grantRole(exec.EXECUTOR_ROLE(), bot);

        MockBridgeAdapter allowedBridge = new MockBridgeAdapter();
        MockBridgeAdapter rogueBridge = new MockBridgeAdapter(); // deliberately never allowlisted
        vm.prank(admin);
        exec.setBridgeAdapterAllowed(address(allowedBridge), true);

        vm.deal(address(this), 10 ether);
        SwapStep[] memory noSteps = new SwapStep[](0); // bridge held inventory as-is; no route needed

        // (a) reverts BridgeAdapterNotAllowed when the adapter isn't
        // allowlisted. No inventory needed: this reverts before the
        // bridgeToken balance is even read.
        vm.prank(bot);
        vm.expectRevert(
            abi.encodeWithSelector(CrossChainArbitrageExecutor.BridgeAdapterNotAllowed.selector, address(rogueBridge))
        );
        exec.executeSourceLeg(
            noSteps, address(0), 0, address(rogueBridge), POLYGON_WMATIC, 1, 8453, wrongRecipient, ""
        );

        // (c) both new guardian setters revert for the executor bot.
        vm.prank(bot);
        vm.expectRevert(); // AccessControlUnauthorizedAccount
        exec.setBridgeAdapterAllowed(address(rogueBridge), true);
        vm.prank(bot);
        vm.expectRevert(); // AccessControlUnauthorizedAccount
        exec.setSiblingExecutor(8453, sibling);

        // (d) reverts SiblingMismatch once a sibling IS registered for
        // dstChainId and dstRecipient doesn't match it (also reverts before
        // the balance read, so still no inventory needed).
        vm.prank(admin);
        exec.setSiblingExecutor(8453, sibling);
        vm.prank(bot);
        vm.expectRevert(
            abi.encodeWithSelector(CrossChainArbitrageExecutor.SiblingMismatch.selector, wrongRecipient, sibling)
        );
        exec.executeSourceLeg(
            noSteps, address(0), 0, address(allowedBridge), POLYGON_WMATIC, 1, 8453, wrongRecipient, ""
        );

        // (e) succeeds once dstRecipient matches the registered sibling.
        IWrappedNative(POLYGON_WMATIC).deposit{value: 1 ether}();
        IWrappedNative(POLYGON_WMATIC).transfer(address(exec), 1 ether);
        vm.prank(bot);
        exec.executeSourceLeg(noSteps, address(0), 0, address(allowedBridge), POLYGON_WMATIC, 1, 8453, sibling, "");
        assertEq(
            IERC20(POLYGON_WMATIC).balanceOf(address(exec)), 0, "source executor should hold no WMATIC after bridging"
        );

        // (f) backward-compat: an unregistered dstChainId imposes no
        // constraint on dstRecipient — matches every existing test (and
        // every call before this registry existed).
        assertEq(exec.siblingExecutor(999), address(0), "chain 999 should have no registered sibling");
        IWrappedNative(POLYGON_WMATIC).deposit{value: 1 ether}();
        IWrappedNative(POLYGON_WMATIC).transfer(address(exec), 1 ether);
        vm.prank(bot);
        exec.executeSourceLeg(
            noSteps, address(0), 0, address(allowedBridge), POLYGON_WMATIC, 1, 999, wrongRecipient, ""
        );
        assertEq(
            IERC20(POLYGON_WMATIC).balanceOf(address(exec)), 0, "source executor should hold no WMATIC after bridging"
        );
    }
}
