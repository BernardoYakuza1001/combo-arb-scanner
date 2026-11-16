import { chainConfigs } from "@morpho-blue-liquidation-bot/config";
import dotenv from "dotenv";
import { appendFileSync, readFileSync } from "node:fs";
import { createWalletClient, type Hex, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";

import { deploy } from "./utils/deploy-executor.js";

async function run() {
  dotenv.config();

  const configs = Object.values(chainConfigs);

  for (const config of configs) {
    const chain = config.chain;
    const id = chain.id;

    const rpcUrl = process.env[`RPC_URL_${id}`];
    const privateKey = process.env[`LIQUIDATION_PRIVATE_KEY_${id}`];

    // Only chains the user has set up (RPC + key) get an executor; existing ones are not redeployed.
    if (!rpcUrl || !privateKey) continue;
    if (process.env[`EXECUTOR_ADDRESS_${id}`]) {
      console.log(`Executor already set for chain ${id}: ${process.env[`EXECUTOR_ADDRESS_${id}`]}`);
      continue;
    }

    const client = createWalletClient({
      chain,
      transport: http(rpcUrl),
      account: privateKeyToAccount(privateKey as Hex),
    });

    const address = await deploy(client, privateKeyToAccount(privateKey as Hex).address);
    if (address) {
      const env = readFileSync(".env", "utf8");
      appendFileSync(".env", `${env.endsWith("\n") ? "" : "\n"}EXECUTOR_ADDRESS_${id}=${address}\n`);
      console.log(`Saved EXECUTOR_ADDRESS_${id} to .env`);
    }
  }
}

void run();
