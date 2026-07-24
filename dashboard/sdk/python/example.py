"""Example: read stats and live-tune settings from Python.

Run the backend first (`pnpm --filter @l2/backend dev`), then:
    python sdk/python/example.py
"""
from l2_arbitrage import L2ArbitrageClient

client = L2ArbitrageClient(base_url="http://localhost:8787")

print("health:", client.health())

settings = client.get_settings()
print("current loan size:", settings["loanAmountUsd"], "USD")

# Live-tune parameters — effective on the next scan, no restart.
updated = client.update_settings({"minProfitUsd": 40, "networks": ["base", "arbitrum"]})
print("updated networks:", updated["networks"])

opps = client.opportunities(limit=5)
print(f"{opps['total']} active opportunities")
for o in opps["opportunities"]:
    route = " -> ".join(leg["dex"] for leg in o["route"])
    print(f"  [{o['network']}] {route}  net ${o['netProfitUsd']:.2f}")
