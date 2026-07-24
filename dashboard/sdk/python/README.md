# l2-arbitrage-sdk — Python client

Standard-library-only REST client for the L2 Arbitrage GUI API. Optional
real-time streaming with `websocket-client`.

```bash
pip install -e sdk/python            # REST only, zero dependencies
pip install -e "sdk/python[stream]"  # add WebSocket streaming
```

```python
from l2_arbitrage import L2ArbitrageClient

client = L2ArbitrageClient(base_url="http://localhost:8787")

# Read + live-tune settings (effective on the next scan, no restart).
client.update_settings({"minProfitUsd": 40, "networks": ["base", "arbitrum"]})

for opp in client.opportunities(limit=5)["opportunities"]:
    print(opp["network"], opp["netProfitUsd"])

# Optional real-time stream (needs websocket-client):
# client.stream(lambda msg: print(msg["type"]))
```

See [`example.py`](./example.py) for a runnable script.
