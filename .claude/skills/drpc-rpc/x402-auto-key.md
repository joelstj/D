# x402 Auto-Key Acquisition

Automatically acquire a DRPC API key using the x402 protocol. Requires the agent to have wallet access (private key or signer). If no wallet is available, skip this flow and return to SKILL.md Step 3 (ask user for key).

## Overview

```
1. Check wallet access → no wallet → abort, go to fallback
2. SIWE sign-in → 200 → got api_key → done
3. SIWE returns 402 → parse payment requirements
4. EIP-3009 payment → 200 → got api_key → done
5. Any failure → abort, go to fallback
```

## Endpoint

All requests go to the same endpoint:

```
GET https://drpc.org/api/x402
```

Behavior is determined by which header is present.

## Step 1: Check Wallet Access

The agent must have access to an EVM wallet — a private key, mnemonic, or external signer that can produce ECDSA signatures.

If no wallet is available, **stop here** and return to SKILL.md Step 3 (fallback: ask user for API key).

## Step 2: SIWE Sign-In (Free — No Payment)

Construct an EIP-4361 (Sign-In With Ethereum) message and sign it. This checks if the wallet is already known to DRPC.

### Construct the SIWE message

```json
{
  "message": {
    "domain": "drpc.org",
    "address": "0xYOUR_WALLET_ADDRESS",
    "statement": "Sign in to dRPC AI",
    "uri": "https://drpc.org/api/x402",
    "version": "1",
    "chainId": 1,
    "nonce": "drpcaifirst",
    "issuedAt": "2026-04-16T12:00:00Z"
  }
}
```

- `address`: your wallet address (checksummed)
- `issuedAt`: current UTC timestamp in ISO 8601
- All other fields are fixed constants

### Sign the message

Sign the **EIP-191 personal_sign** hash of the plaintext SIWE string:

```
drpc.org wants you to sign in with your Ethereum account:
0xYOUR_WALLET_ADDRESS

Sign in to dRPC AI

URI: https://drpc.org/api/x402
Version: 1
Chain ID: 1
Nonce: drpcaifirst
Issued At: 2026-04-16T12:00:00Z
```

The signature is a 65-byte hex string (`0x...`, with `v` = 27 or 28).

### Send the request

```bash
curl -s -X GET "https://drpc.org/api/x402" \
  -H "Content-Type: application/json" \
  -H "wallet-signature: {\"message\":{\"domain\":\"drpc.org\",\"address\":\"0xYOUR_ADDRESS\",\"statement\":\"Sign in to dRPC AI\",\"uri\":\"https://drpc.org/api/x402\",\"version\":\"1\",\"chainId\":1,\"nonce\":\"drpcaifirst\",\"issuedAt\":\"2026-04-16T12:00:00Z\"},\"signature\":\"0xSIGNATURE_HEX\"}"
```

### Handle the response

- **HTTP 200** → wallet is known. Parse `api_key` from response body:
  ```json
  { "owner_id": "...", "api_key": "YOUR_API_KEY" }
  ```
  **Done.** Return `api_key` to SKILL.md Step 4.

- **HTTP 402** → wallet is not registered. The response body contains payment requirements. Continue to Step 3.

- **Any other error** → abort, return to SKILL.md Step 3 (fallback).

## Step 3: Parse Payment Requirements

The 402 response body is a JSON array of accepted payment schemes:

```json
[
  {
    "scheme": "exact",
    "network": "eip155:8453",
    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "amount": "5000000",
    "payTo": "0xB12C13975A4e928BC09432Dbd1eD3fd832Bf99e5",
    "maxTimeoutSeconds": 60,
    "extra": {
      "name": "USD Coin",
      "version": "2"
    }
  }
]
```

Pick one entry. Prefer `eip155:8453` (Base mainnet) if available.

Key fields you need for the next step:
- `network` — CAIP-2 chain identifier
- `asset` — token contract address (USDC)
- `amount` — payment amount in smallest units (5000000 = 5 USDC)
- `payTo` — recipient address
- `extra.name` — token name for EIP-712 domain (e.g. `"USD Coin"`)
- `extra.version` — token version for EIP-712 domain (e.g. `"2"`)

## Step 4: EIP-3009 Payment Signature

Sign an EIP-3009 `transferWithAuthorization` to pay for the API key.

### Construct the authorization

```json
{
  "from": "0xYOUR_WALLET_ADDRESS",
  "to": "PAYTO_FROM_REQUIREMENTS",
  "value": "AMOUNT_FROM_REQUIREMENTS",
  "validAfter": "0",
  "validBefore": "UNIX_TIMESTAMP_NOW_PLUS_300",
  "nonce": "0xRANDOM_32_BYTES_HEX"
}
```

- `to` and `value` must match the requirements exactly
- `validBefore`: current unix timestamp + 300 (5 minutes)
- `nonce`: 32 random bytes, hex-encoded with `0x` prefix

### Sign EIP-712 typed data

Domain:
```json
{
  "name": "USD Coin",
  "version": "2",
  "chainId": 8453,
  "verifyingContract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
}
```

- `name` and `version` come from `requirements.extra`
- `chainId` is the numeric part of `requirements.network` (e.g. `eip155:8453` → `8453`)
- `verifyingContract` is `requirements.asset`

Primary type `TransferWithAuthorization`:
```json
{
  "TransferWithAuthorization": [
    { "name": "from", "type": "address" },
    { "name": "to", "type": "address" },
    { "name": "value", "type": "uint256" },
    { "name": "validAfter", "type": "uint256" },
    { "name": "validBefore", "type": "uint256" },
    { "name": "nonce", "type": "bytes32" }
  ]
}
```

Sign the EIP-712 hash with your private key. The signature is 65 bytes (`v` = 27 or 28).

### Build the x402 PaymentPayload

```json
{
  "x402Version": 2,
  "payload": {
    "signature": "0xSIGNATURE_HEX",
    "authorization": {
      "from": "0xYOUR_ADDRESS",
      "to": "PAYTO",
      "value": "AMOUNT",
      "validAfter": "0",
      "validBefore": "TIMESTAMP",
      "nonce": "0xRANDOM_NONCE"
    }
  },
  "accepted": {
    "scheme": "exact",
    "network": "eip155:8453",
    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "amount": "AMOUNT",
    "payTo": "PAYTO",
    "maxTimeoutSeconds": 60,
    "extra": {
      "name": "USD Coin",
      "version": "2"
    }
  }
}
```

The `accepted` block is copied verbatim from the requirements.

Base64-encode the JSON payload.

### Send the request

```bash
curl -s -X GET "https://drpc.org/api/x402" \
  -H "Content-Type: application/json" \
  -H "payment-signature: BASE64_ENCODED_PAYLOAD"
```

### Handle the response

- **HTTP 200** → payment accepted. Parse `api_key`:
  ```json
  {
    "owner_id": "...",
    "api_key": "YOUR_API_KEY",
    "tx_hash": "0x...",
    "network": "eip155:8453",
    "payer": "0x..."
  }
  ```
  **Done.** Return `api_key` to SKILL.md Step 4.

- **Any error** → abort, return to SKILL.md Step 3 (fallback).

## Error Reference

| HTTP | Meaning | Action |
|------|---------|--------|
| 200 | Success | Parse `api_key` from body |
| 402 | Payment required / failed | After SIWE: continue to payment. After payment: fallback |
| 400 | Bad request | Check JSON format, abort to fallback |
| 500 | Server error | Abort to fallback |
