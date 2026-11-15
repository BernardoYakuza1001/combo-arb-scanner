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

def main():
    print(__doc__)
    if os.path.exists(ENV) and input("pmmm\\.env already exists. Replace it? (y/n): ").strip().lower() != "y": return
    key = None
    for _ in range(5):
        key = clean_key(getpass.getpass("1) Paste your PRIVATE KEY (right-click to paste; nothing will appear) then Enter: "))
        if key: print("   Key accepted (66 characters, hidden)."); break
        print("   That is not a private key: it must be 64 hex characters, with or without 0x in front. Try again.")
    if not key: print("Stopped: no valid key."); return
    addr = None
    for _ in range(5):
        addr = clean_addr(input("2) Paste your Polymarket WALLET ADDRESS (0x..., 42 characters) then Enter: "))
        if addr: break
        print("   That is not a wallet address: it must be 0x followed by 40 hex characters. Try again.")
    if not addr: print("Stopped: no valid address."); return
    sig = None
    try:
        from eth_account import Account
        signer = Account.from_key(key).address
        print(f"   Your key's signer address is {signer[:6]}...{signer[-4:]} (Polymarket shows it as 'Signer Address').")
        if signer.lower() == addr.lower():
            print("   You pasted the SIGNER address. On a normal Polymarket account the money sits in a different address:")
            print("   the wallet address on your Profile / Deposit page. Paste that one instead.")
            again = clean_addr(input("   Wallet address from Profile (or just press Enter if you really trade from a plain wallet): "))
            if again and again.lower() != signer.lower(): addr = again
            else: sig = "0"; print("   Using the signer address itself (wallet type 0).")
    except Exception: pass
    while sig is None:
        a = input("3) How do you log into Polymarket?  1 = email or Google   2 = MetaMask / browser wallet : ").strip()
        if a in ("1", "2"): sig = a
    with open(ENV, "w", encoding="utf-8") as f:
        f.write(f"PM_PRIVATE_KEY={key}\nPM_FUNDER={addr}\nPM_SIG_TYPE={sig}\n")
    print(f"\nSaved to {ENV}\nWallet {addr[:6]}...{addr[-4:]}, type {sig}. Next: PREFLIGHT.bat (it places no orders).")

if __name__ == "__main__":
    main()
