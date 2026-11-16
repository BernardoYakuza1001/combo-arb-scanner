"""Shows the bot wallet's ADDRESS (never the key), from liqbot\\.env on this PC."""
import os
from eth_account import Account
env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
key = next((l.split("=", 1)[1].strip() for l in open(env, encoding="utf-8") if l.startswith("LIQUIDATION_PRIVATE_KEY_8453=")), None)
if not key: print("No key in liqbot\\.env yet - run SETUP first.")
else:
    a = Account.from_key(key).address
    print("\nBot wallet address (send ETH on the BASE network here):\n\n   " + a + "\n")
    open(os.path.join(os.path.dirname(env), "BOT_ADDRESS.txt"), "w").write(a + "\n")
