// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {Script, console2} from "forge-std/Script.sol";
import {FlashLoanArbitrage} from "../contracts/FlashLoanArbitrage.sol";
import {CrossChainArbitrageExecutor} from "../contracts/crosschain/CrossChainArbitrageExecutor.sol";

/// @title Deploy
/// @notice Foundry deployment script for FlashLoanArbitrage AND
///         CrossChainArbitrageExecutor.
/// @dev Provide the flash-loan provider addresses and admin via env vars, then:
///
///   AAVE_POOL=0x... BALANCER_VAULT=0x... ADMIN=0x... \
///   ARBITRUM_RPC_URL="https://arb1.arbitrum.io/rpc" \
///   forge script script/Deploy.s.sol:Deploy \
///     --rpc-url "$ARBITRUM_RPC_URL" --broadcast --verify -vvvv
///
///   ARBITRUM_RPC_URL is not read from any repo .env by forge — set it in this
///   same shell invocation. Left unset, `--rpc-url` silently receives an empty
///   string and forge fails with a confusing "Internal transport error ...
///   os error 2" pointing at your current directory, not a clear missing-var
///   error.
///
///   At least one of AAVE_POOL / BALANCER_VAULT must be non-zero. VERIFY every
///   address against official protocol docs first (see config/addresses.js).
///
///   CrossChainArbitrageExecutor needs one sibling deployment per chain you
///   bridge between — run this script once per chain (e.g. once with
///   --rpc-url "$ARBITRUM_RPC_URL" and once with --rpc-url "$POLYGON_RPC_URL")
///   to get the pair of executors the cross-chain flow needs. Set
///   SKIP_CROSSCHAIN=1 to deploy only FlashLoanArbitrage, matching this
///   script's pre-existing behavior.
contract Deploy is Script {
    function run() external returns (FlashLoanArbitrage arb, CrossChainArbitrageExecutor xchain) {
        address aavePool = vm.envOr("AAVE_POOL", address(0));
        address balancerVault = vm.envOr("BALANCER_VAULT", address(0));
        // Default admin to the broadcasting key if ADMIN is unset.
        address admin = vm.envOr("ADMIN", msg.sender);
        bool skipCrossChain = vm.envOr("SKIP_CROSSCHAIN", false);

        require(aavePool != address(0) || balancerVault != address(0), "Set AAVE_POOL and/or BALANCER_VAULT");

        vm.startBroadcast();
        arb = new FlashLoanArbitrage(aavePool, balancerVault, admin);
        if (!skipCrossChain) {
            xchain = new CrossChainArbitrageExecutor(admin);
        }
        vm.stopBroadcast();

        console2.log("FlashLoanArbitrage:        ", address(arb));
        console2.log("  aavePool:                ", aavePool);
        console2.log("  balancerVault:           ", balancerVault);
        console2.log("  admin:                   ", admin);
        if (!skipCrossChain) {
            console2.log("CrossChainArbitrageExecutor:", address(xchain));
        }
    }
}
