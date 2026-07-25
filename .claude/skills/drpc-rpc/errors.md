# DRPC Error Codes & Troubleshooting

## Billing & Quota Errors

| Code | HTTP | Error | Action |
|------|------|-------|--------|
| 10 | 403 | User balance exceeded | Top up at https://drpc.org/billing. Do NOT retry. |
| 53 | 403 | Free monthly limit reached | Upgrade to paid plan at https://drpc.org/billing |
| 24 | 403 | Daily key limit exceeded | Wait until tomorrow, or increase limit in dashboard |
| 52 | 403 | Subscription limit exceeded | Upgrade subscription tier |

## Rate Limiting

| Code | HTTP | Error | Action |
|------|------|-------|--------|
| 31 | 429 | Too many requests | Retry after 1-2 seconds with exponential backoff |
| 46 | 408 | Free tier timeout | Request took too long on free tier. Upgrade for faster responses |

## Feature Restrictions

| Code | HTTP | Error | Action |
|------|------|-------|--------|
| 51 | 400 | Paid feature only | This feature requires a paid plan |
| 47 | 403 | Batch >3 not allowed on free tier | Use sequential calls instead, or upgrade |
| 48 | 403 | Method not allowed for this key | Method restricted by key settings |
| 50 | 403 | Chain not allowed for this key | Chain restricted by key settings |
| 29 | 400 | Feature not allowed on your plan | Upgrade plan |

## Authentication Errors

| Code | HTTP | Error | Action |
|------|------|-------|--------|
| 4 | 403 | Invalid token | API key is wrong. Ask user to check at drpc.org dashboard |
| 32 | 403 | Token not found / expired | Generate new key at drpc.org dashboard |
| 9 | 403 | Key deactivated | Re-enable key in dashboard |

## Network & Request Errors

| Code | HTTP | Error | Action |
|------|------|-------|--------|
| 11 | 400 | Unknown network | Call `list_networks` to find correct network name |
| 0 | 400 | Couldn't parse request | Check JSON format |
| 5 | 400 | Invalid request | Verify request structure |
| 38 | 400 | eth_getLogs >10,000 logs | Narrow the block range |

## Provider Errors

| Code | HTTP | Error | Action |
|------|------|-------|--------|
| 1 | 500 | Not enough providers | Network may be temporarily unavailable. Retry later |
| 2 | 500 | Provider request error | Upstream node error. Retry once |
| 13 | 400 | Can't route to provider | If using bounded providers, check the provider list |
| 40 | 408 | Request timeout | Retry once. If persistent, simplify the query |

## Recovery Patterns

### Balance / quota exhausted (codes 10, 53, 52)
1. Inform user their balance or quota is exhausted
2. Provide link: https://drpc.org/billing
3. Do NOT retry — will keep failing until topped up or upgraded

### Rate limited (code 31)
1. Wait 1-2 seconds
2. Retry the request
3. If still failing, space out subsequent requests

### Authentication failed (codes 4, 32, 9)
1. Ask user to verify their API key at https://drpc.org dashboard
2. Offer to reconfigure MCP — see [setup.md](setup.md)

### Unknown network (code 11)
1. Call `list_networks` to get available networks
2. Find the closest match to what user requested
3. Retry with the correct network name

### Free tier limits (codes 46, 47, 51)
1. Explain the limitation to user
2. Suggest upgrading at https://drpc.org/billing
3. For batch limits: switch to sequential calls as a workaround
