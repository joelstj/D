// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {Script, console2} from "forge-std/Script.sol";
import {FlashLoanArbitrage} from "../contracts/FlashLoanArbitrage.sol";

/// @title Deploy
/// @notice Foundry deployment script for FlashLoanArbitrage.
/// @dev Provide the flash-loan provider addresses and admin via env vars, then:
///
///   AAVE_POOL=0x... BALANCER_VAULT=0x... ADMIN=0x... \
///   forge script script/Deploy.s.sol:Deploy \
///     --rpc-url "$ARBITRUM_RPC_URL" --broadcast --verify -vvvv
///
///   At least one of AAVE_POOL / BALANCER_VAULT must be non-zero. VERIFY every
///   address against official protocol docs first (see config/addresses.js).
contract Deploy is Script {
    function run() external returns (FlashLoanArbitrage arb) {
        address aavePool = vm.envOr("AAVE_POOL", address(0));
        address balancerVault = vm.envOr("BALANCER_VAULT", address(0));
        // Default admin to the broadcasting key if ADMIN is unset.
        address admin = vm.envOr("ADMIN", msg.sender);

        require(
            aavePool != address(0) || balancerVault != address(0),
            "Set AAVE_POOL and/or BALANCER_VAULT"
        );

        vm.startBroadcast();
        arb = new FlashLoanArbitrage(aavePool, balancerVault, admin);
        vm.stopBroadcast();

        console2.log("FlashLoanArbitrage:", address(arb));
        console2.log("  aavePool:       ", aavePool);
        console2.log("  balancerVault:  ", balancerVault);
        console2.log("  admin:          ", admin);
    }
}
