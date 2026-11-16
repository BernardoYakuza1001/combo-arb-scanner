"""Creates a brand-new wallet for the bot on THIS PC and saves it in liqbot\\.env together with the
Alchemy URL from ..\\alchemy_url.txt. Shows only the ADDRESS. The key stays in liqbot\\.env (keep that file)."""
import os, json, urllib.request
from eth_account import Account
HERE = os.path.dirname(os.path.abspath(__file__)); ENV = os.path.join(HERE, ".env")
if os.path.exists(ENV) and "LIQUIDATION_PRIVATE_KEY_8453=" in open(ENV, encoding="utf-8").read():
    key = next(l.split("=", 1)[1].strip() for l in open(ENV, encoding="utf-8") if l.startswith("LIQUIDATION_PRIVATE_KEY_8453="))
    print("A bot wallet already exists - keeping it.")
else:
    url = open(os.path.join(os.path.dirname(HERE), "alchemy_url.txt"), encoding="utf-8").read().strip()
    req = urllib.request.Request(url, data=b'{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}', headers={"Content-Type": "application/json"})
    ok = json.load(urllib.request.urlopen(req, timeout=15)).get("result") == "0x2105"
    print("Alchemy Base connection:", "OK" if ok else "NOT OK (check alchemy_url.txt)")
    acct = Account.create(); key = acct.key.hex(); key = key if key.startswith("0x") else "0x" + key
    open(ENV, "w", encoding="utf-8").write(f"RPC_URL_8453={url}\nLIQUIDATION_PRIVATE_KEY_8453={key}\n")
    print("New bot wallet created and saved in liqbot\\.env")
addr = Account.from_key(key).address
open(os.path.join(HERE, "BOT_ADDRESS.txt"), "w").write(addr + "\n")
print("\n=====================================================")
print(" BOT ADDRESS - send $15-20 of ETH here, on the BASE network:\n")
print("   " + addr)
print("\n It is also saved in liqbot\\BOT_ADDRESS.txt (open it and copy).")
print("=====================================================\n")
