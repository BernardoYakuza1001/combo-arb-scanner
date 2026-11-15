"""Creates pmmm\\.env on THIS computer. Run SETUP_KEY.bat.

You paste your Polymarket private key and wallet address into this window. They are written only to
the file pmmm\\.env on this PC. The key is never shown on screen, never logged and never sent anywhere.
"""
import os, re, sys, getpass
HERE = os.path.dirname(os.path.abspath(__file__)); ENV = os.path.join(HERE, ".env")

def clean_key(k):
    k = re.sub(r"\s+", "", k or "").strip('"').strip("'")
    if re.fullmatch(r"[0-9a-fA-F]{64}", k): k = "0x" + k
    return k if re.fullmatch(r"0x[0-9a-fA-F]{64}", k) else None

def clean_addr(a):
    a = re.sub(r"\s+", "", a or "").strip('"').strip("'")
    return a if re.fullmatch(r"0x[0-9a-fA-F]{40}", a) else None

def candidates(signer):
    """Possible Polymarket wallet addresses for a signer: (wallet type, address)."""
    from eth_utils import keccak, to_checksum_address
    from eth_abi import encode
    def c2(factory, salt, codehash):
        return to_checksum_address(keccak(b"\xff" + bytes.fromhex(factory[2:]) + salt + bytes.fromhex(codehash[2:]))[12:])
    raw = bytes.fromhex(signer[2:])
    return [("1", c2("0xaB45c5A4B0c941a2F231C04C3f49182e1A254052", keccak(raw), "0xd21df8dc65880a8606f09fe0ce3df9b8869287ab0b058be05aa9e8af6330a00b")),
            ("2", c2("0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b", keccak(encode(["address"], [signer])), "0x2bce2127ff07fb632d16c8347c4ebf501f4841168bed00d9e6ef715ddb6fcecf")),
            ("0", signer)]

def balance(key, sig, funder):
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
    c = ClobClient("https://clob.polymarket.com", key=key, chain_id=137, signature_type=int(sig), funder=funder)
    c.set_api_creds(c.create_or_derive_api_creds())
    p = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=int(sig))
    try: c.update_balance_allowance(p)
    except Exception: pass
    return float(c.get_balance_allowance(p).get("balance", 0)) / 1e6

def detect(key):
    from eth_account import Account
    signer = Account.from_key(key).address
    print(f"   Signer address {signer[:6]}...{signer[-4:]}. Looking for the wallet that holds your USDC...")
    best = None
    try:        # accounts created after May 2026: Deposit Wallet, via Polymarket's unified client
        from polymarket import SecureClient
        with SecureClient.create(private_key=key) as c:
            w = str(c.wallet); bal = float(c.get_balance_allowance(asset_type="COLLATERAL").balance) / 1e6
        print(f"   type 3 (Deposit Wallet) {w[:6]}...{w[-4:]}: ${bal:,.2f}")
        if bal > 0: best = (w, "3", bal)
    except Exception as e:
        print(f"   type 3 (Deposit Wallet): no answer ({str(e)[:120]})")
    if best: return best
    for sig, addr in candidates(signer):
        try: bal = balance(key, sig, addr)
        except Exception as e: print(f"   type {sig}: no answer ({str(e)[:80]})"); continue
        print(f"   type {sig} {addr[:6]}...{addr[-4:]}: ${bal:,.2f}")
        if bal > 0 and (best is None or bal > best[2]): best = (addr, sig, bal)
    return best

def main():
    print(__doc__)
    if os.path.exists(ENV) and input("pmmm\\.env already exists. Replace it? (y/n): ").strip().lower() != "y": return
    key = None
    for _ in range(5):
        key = clean_key(getpass.getpass("") if os.environ.get("PMMM_HIDE_KEY") == "YES" else
                        input("1) Paste your PRIVATE KEY here (Ctrl+V or right-click), then press Enter:\n> "))
        if key:
            os.system("cls" if os.name == "nt" else "clear")       # wipe the key off the screen
            print("Key accepted (66 characters). The screen was cleared so the key is no longer visible."); break
        print("   That is not a private key: it must be 64 hex characters, with or without 0x in front. Try again.")
    if not key: print("Stopped: no valid key."); return
    addr = sig = None
    # Find the wallet automatically: derive the possible wallet addresses from the key and ask Polymarket
    # which one holds the money. Nothing but public addresses is sent.
    try:
        found = detect(key)
        if found:
            addr, sig, bal = found
            print(f"   Found your Polymarket wallet {addr[:6]}...{addr[-4:]} with ${bal:,.2f} (wallet type {sig}).")
        else:
            print("   Could not find a funded wallet automatically.")
    except Exception as e:
        print("   Automatic wallet search failed:", str(e)[:150])
    while not addr:
        addr = clean_addr(input("2) Paste your Polymarket WALLET ADDRESS from your Profile page (0x..., 42 characters) then Enter: "))
        if not addr: print("   That is not a wallet address: it must be 0x followed by 40 hex characters. Try again.")
    while sig is None:
        a = input("3) How do you log into Polymarket?  1 = email or Google   2 = MetaMask / browser wallet : ").strip()
        if a in ("1", "2"): sig = a
    with open(ENV, "w", encoding="utf-8") as f:
        f.write(f"PM_PRIVATE_KEY={key}\nPM_FUNDER={addr}\nPM_SIG_TYPE={sig}\n")
    print(f"\nSaved to {ENV}\nWallet {addr[:6]}...{addr[-4:]}, type {sig}. Next: PREFLIGHT.bat (it places no orders).")

if __name__ == "__main__":
    main()
