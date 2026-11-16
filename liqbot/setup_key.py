"""Creates liqbot\\.env on THIS computer for the Base liquidation bot. Run SETUP.bat.

Use a NEW wallet that holds only gas money (about $15-20 of ETH on Base). The key is written only to
liqbot\\.env on this PC; it is wiped from the screen after you paste it and is never logged or sent."""
import os, re, json, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); ENV = os.path.join(HERE, ".env")

def clean_key(k):
    k = re.sub(r"\s+", "", k or "").strip('"').strip("'")
    if re.fullmatch(r"[0-9a-fA-F]{64}", k): k = "0x" + k
    return k if re.fullmatch(r"0x[0-9a-fA-F]{64}", k) else None

def rpc_ok(url):
    try:
        req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=15)).get("result") == "0x2105"   # 8453 = Base
    except Exception as e:
        print("   RPC check failed:", str(e)[:120]); return False

def main():
    print(__doc__)
    if os.path.exists(ENV) and input("liqbot\\.env already exists. Replace it? (y/n): ").strip().lower() != "y": return
    key = None
    while not key:
        key = clean_key(input("1) Paste the PRIVATE KEY of the new bot wallet (Ctrl+V), then Enter:\n> "))
        if not key: print("   That is not a private key (64 hex characters, with or without 0x). Try again.")
    os.system("cls" if os.name == "nt" else "clear"); print("Key accepted. Screen cleared.")
    url = None
    pre = os.path.join(os.path.dirname(HERE), "alchemy_url.txt")     # optional, pre-filled next to the liqbot folder
    if os.path.exists(pre):
        u = open(pre, encoding="utf-8").read().strip()
        if u.startswith("https://") and rpc_ok(u): url = u; print("2) Alchemy Base URL found in alchemy_url.txt and tested: OK")
        else: print("   alchemy_url.txt did not answer as Base mainnet; please paste the URL.")
    while not url:
        u = input("2) Paste your Alchemy Base Mainnet URL (https://base-mainnet.g.alchemy.com/v2/...), then Enter:\n> ").strip()
        if u.startswith("https://") and rpc_ok(u): url = u
        else: print("   That URL did not answer as Base mainnet. Copy it again from Alchemy (network: Base Mainnet).")
    open(ENV, "w", encoding="utf-8").write(f"RPC_URL_8453={url}\nLIQUIDATION_PRIVATE_KEY_8453={key}\n")
    print(f"\nSaved {ENV}. Next: DEPLOY_EXECUTOR.bat (needs a little ETH on Base in the bot wallet).")

if __name__ == "__main__":
    main()
