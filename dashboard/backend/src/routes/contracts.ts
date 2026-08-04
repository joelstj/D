import { Router, type Response } from "express";
import type { ContractService } from "../contracts/service";

/**
 * REST surface for the dashboard **Contracts** panel: status monitor, server-side
 * compile, artifact serving (for a browser-wallet deploy), deploy-argument
 * resolution, deployment recording (writes `.env` + `deployments/*.json`), and a
 * read-only multi-chain readiness sweep (the deployment stress test).
 *
 * Safety (invariant 3 — binding): nothing here signs or broadcasts. Compilation
 * touches no chain; deployment is signed by the operator's MetaMask in the
 * browser and only its *public result* (an address) is recorded here. The
 * readiness sweep is strictly read-only (`getCode` + a `staticCall` view).
 */
export function createContractsRouter(service: ContractService): Router {
  const router = Router();

  router.get("/status", (_req, res) => guard(res, () => res.json(service.status())));

  router.post("/compile", (_req, res) => guard(res, async () => res.json(await service.compile())));

  router.get("/artifact/:name", (req, res) =>
    guard(res, () => res.json(service.getArtifact(req.params.name))),
  );

  // Deploy arguments for a browser-wallet deploy of an executor contract
  // (`?contract=` — defaults to the atomic `FlashLoanArbitrage`; pass
  // `CrossChainArbitrageExecutor` for the cross-chain executor's 1-arg
  // constructor). `admin` is the connected wallet (becomes admin/guardian/
  // executor); the atomic contract's provider addresses come from the
  // verified address book — an unverified chain 400s so the UI blocks.
  router.get("/deploy-params/:network", (req, res) =>
    guard(res, () => {
      const admin = typeof req.query.admin === "string" ? req.query.admin : "";
      const contract = typeof req.query.contract === "string" ? req.query.contract : undefined;
      res.json(service.deployParams(req.params.network, admin, contract));
    }),
  );

  // Record a deployment the operator's wallet already broadcast.
  router.post("/deployment", (req, res) =>
    guard(res, () => {
      const b = req.body ?? {};
      const result = service.recordDeployment({
        network: b.network,
        chainId: Number(b.chainId),
        address: b.address,
        crossChainAddress: b.crossChainAddress ?? null,
        deployer: b.deployer,
        txHash: b.txHash,
        deployedAt: b.deployedAt,
      });
      res.json(result);
    }),
  );

  // Read-only deployment stress test across every chain with a recorded deploy.
  router.get("/readiness", (_req, res) =>
    guard(res, async () => res.json(await service.runReadiness())),
  );

  return router;
}

async function guard(res: Response, fn: () => unknown | Promise<unknown>) {
  try {
    await fn();
  } catch (err) {
    res.status(400).json({ error: "bad_request", message: String((err as Error).message) });
  }
}
