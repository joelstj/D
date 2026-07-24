"""
Plug-and-play integration example (Python / web3.py).

Mirrors integration/examples/bot.js. Any language with an EVM ABI encoder can
drive the contract the same way: build the SwapStep[] route, simulate with a
call (eth_call), then send executeArbitrage.

    pip install web3
    ARB_ADDRESS=0x... RPC_URL=... PRIVATE_KEY=... python integration/examples/bot.py
"""
import json
import os
import time
from pathlib import Path

from web3 import Web3

ABI = json.loads((Path(__file__).parent.parent / "abi" / "FlashLoanArbitrage.abi.json").read_text())

# Enums (must match contracts/libraries/ArbTypes.sol)
AAVE_V3, BALANCER_V2 = 0, 1
UNISWAP_V2, UNISWAP_V3_SINGLE, UNISWAP_V3_MULTI, CURVE, GENERIC = 0, 1, 2, 3, 4


def v2(router, token_in, token_out, min_out=0):
    # SwapStep tuple order must match the ABI exactly.
    return (UNISWAP_V2, router, token_in, token_out, 0, 0, 0, min_out, b"", 0)


def v3(router, token_in, token_out, fee, min_out=0):
    return (UNISWAP_V3_SINGLE, router, token_in, token_out, fee, 0, 0, min_out, b"", 0)


def main():
    w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"]))
    acct = w3.eth.account.from_key(os.environ["PRIVATE_KEY"])
    arb = w3.eth.contract(address=Web3.to_checksum_address(os.environ["ARB_ADDRESS"]), abi=ABI)

    # Example addresses (Arbitrum One)
    WETH = Web3.to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    USDCe = Web3.to_checksum_address("0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8")
    UNIV3 = Web3.to_checksum_address("0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45")
    SUSHI = Web3.to_checksum_address("0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506")

    steps = [v3(UNIV3, WETH, USDCe, 500), v2(SUSHI, USDCe, WETH)]

    params = (
        BALANCER_V2,                      # provider
        WETH,                             # asset
        w3.to_wei(1, "ether"),            # amount (or size via quoteOptimalTwoHopV2)
        w3.to_wei(0.002, "ether"),        # minProfit
        acct.address,                     # profitReceiver
        int(time.time()) + 60,            # deadline
        steps,                            # SwapStep[]
    )

    # 1) SIMULATE — a revert means "not profitable right now".
    try:
        arb.functions.executeArbitrage(params).call({"from": acct.address})
    except Exception as e:  # noqa: BLE001 - reference example
        print("Not profitable at this moment:", str(e)[:160])
        return

    # 2) Send it.
    tx = arb.functions.executeArbitrage(params).build_transaction(
        {
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "maxFeePerGas": w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": w3.to_wei(0.01, "gwei"),
        }
    )
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print("submitted:", tx_hash.hex())
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print("mined in block", rcpt.blockNumber)


if __name__ == "__main__":
    main()
