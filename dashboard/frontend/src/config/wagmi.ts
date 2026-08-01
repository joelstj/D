import { http, createConfig } from "wagmi";
import { arbitrum, base, ink, optimism, polygon, unichain } from "wagmi/chains";
import { coinbaseWallet, injected, metaMask } from "wagmi/connectors";

/**
 * wagmi v2 configuration. The first-class `metaMask()` connector uses the
 * **MetaMask SDK** (extension + mobile deep-link/QR), giving full wallet
 * connect/switch/sign operation; `injected()` is kept as a generic EIP-1193
 * fallback for any other browser wallet, and Coinbase Wallet as a third option.
 * Chains match the L2s the engine scans.
 */
export const wagmiConfig = createConfig({
  chains: [base, arbitrum, optimism, polygon, unichain, ink],
  connectors: [
    metaMask({ dappMetadata: { name: "L2 Arbitrage Dashboard" } }),
    injected(),
    coinbaseWallet({ appName: "L2 Arbitrage GUI" }),
  ],
  transports: {
    [base.id]: http(),
    [arbitrum.id]: http(),
    [optimism.id]: http(),
    [polygon.id]: http(),
    [unichain.id]: http(),
    [ink.id]: http(),
  },
  ssr: false,
});

/** Map an EVM chain id to our internal network key. */
export const CHAIN_ID_TO_KEY: Record<number, string> = {
  [base.id]: "base",
  [arbitrum.id]: "arbitrum",
  [optimism.id]: "optimism",
  [polygon.id]: "polygon",
  [unichain.id]: "unichain",
  [ink.id]: "ink",
};

export const KEY_TO_CHAIN_ID: Record<string, number> = {
  base: base.id,
  arbitrum: arbitrum.id,
  optimism: optimism.id,
  polygon: polygon.id,
  unichain: unichain.id,
  ink: ink.id,
};

declare module "wagmi" {
  interface Register {
    config: typeof wagmiConfig;
  }
}
