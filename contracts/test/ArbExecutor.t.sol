// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ArbExecutor} from "../src/core/ArbExecutor.sol";
import {IArbExecutor} from "../src/interfaces/IArbExecutor.sol";

/// @notice Baseline tests for the ArbExecutor walking skeleton. These exercise the parts that are
///         real today: access control, pause, deadline, and route-header validation. Hop dispatch is
///         asserted to revert `NotImplemented` until Phase 2/3 lands it (docs/BUILD_PLAN.md).
contract ArbExecutorTest is Test {
    ArbExecutor internal exec;

    address internal owner = address(0xA11CE);
    address internal operator = address(0xB0B);
    address internal stranger = address(0xBAD);
    address internal newOwner = address(0xC0FFEE);

    function setUp() public {
        exec = new ArbExecutor(owner);
        vm.prank(owner);
        exec.setOperator(operator, true);
    }

    // --- wiring ------------------------------------------------------------
    function test_ownerAndOperatorConfigured() public view {
        assertEq(exec.owner(), owner);
        assertTrue(exec.isOperator(operator));
        assertFalse(exec.paused());
    }

    function test_constructorRejectsZeroOwner() public {
        vm.expectRevert(ArbExecutor.ZeroAddress.selector);
        new ArbExecutor(address(0));
    }

    // --- access control ----------------------------------------------------
    function test_onlyOwnerCanSetOperator() public {
        vm.expectRevert(ArbExecutor.NotOwner.selector);
        vm.prank(stranger);
        exec.setOperator(stranger, true);
    }

    function test_nonOperatorCannotExecute() public {
        vm.expectRevert(IArbExecutor.NotOperator.selector);
        vm.prank(stranger);
        exec.execute(_route(1), 0, block.timestamp + 1, stranger);
    }

    function test_twoStepOwnershipTransfer() public {
        vm.prank(owner);
        exec.transferOwnership(newOwner);
        assertEq(exec.pendingOwner(), newOwner);

        vm.expectRevert(ArbExecutor.NotPendingOwner.selector);
        vm.prank(stranger);
        exec.acceptOwnership();

        vm.prank(newOwner);
        exec.acceptOwnership();
        assertEq(exec.owner(), newOwner);
        assertEq(exec.pendingOwner(), address(0));
    }

    // --- entrypoint guards -------------------------------------------------
    function test_pausedReverts() public {
        vm.prank(owner);
        exec.setPaused(true);
        vm.expectRevert(IArbExecutor.Paused.selector);
        vm.prank(operator);
        exec.execute(_route(1), 0, block.timestamp + 1, operator);
    }

    function test_expiredDeadlineReverts() public {
        vm.warp(1000);
        vm.expectRevert(IArbExecutor.Expired.selector);
        vm.prank(operator);
        exec.execute(_route(1), 0, 999, operator);
    }

    function test_badVersionReverts() public {
        bytes memory route = abi.encodePacked(uint8(0x02), uint8(0), uint8(1));
        vm.expectRevert(abi.encodeWithSelector(IArbExecutor.BadRoute.selector, uint256(0)));
        vm.prank(operator);
        exec.execute(route, 0, block.timestamp + 1, operator);
    }

    function test_zeroHopCountReverts() public {
        vm.expectRevert(abi.encodeWithSelector(IArbExecutor.BadRoute.selector, uint256(1)));
        vm.prank(operator);
        exec.execute(_route(0), 0, block.timestamp + 1, operator);
    }

    function test_tooManyHopsReverts() public {
        vm.expectRevert(abi.encodeWithSelector(IArbExecutor.BadRoute.selector, uint256(1)));
        vm.prank(operator);
        exec.execute(_route(9), 0, block.timestamp + 1, operator);
    }

    function test_shortRouteReverts() public {
        bytes memory route = abi.encodePacked(uint8(1), uint8(0)); // 2 bytes < 3
        vm.expectRevert(abi.encodeWithSelector(IArbExecutor.BadRoute.selector, uint256(2)));
        vm.prank(operator);
        exec.execute(route, 0, block.timestamp + 1, operator);
    }

    function test_wellFormedRouteReachesDispatch() public {
        // Header valid -> passes all guards and hits the not-yet-built dispatch. This proves the
        // header decode works and marks exactly where Phase 2/3 continues.
        vm.expectRevert(IArbExecutor.NotImplemented.selector);
        vm.prank(operator);
        exec.execute(_route(2), 0, block.timestamp + 1, operator);
    }

    // --- helpers -----------------------------------------------------------
    /// @dev Minimal v1 route header: version=1, flags=0, hopCount=`hops`.
    function _route(uint8 hops) internal pure returns (bytes memory) {
        return abi.encodePacked(uint8(1), uint8(0), hops);
    }
}
