import json, requests
API = "https://blue-api.morpho.org/graphql"
def q(s, v=None):
    r = requests.post(API, json={"query": s, "variables": v or {}}, timeout=60); return r.json()
j = q("{__schema{queryType{fields{name args{name type{name kind ofType{name}}}}}}}")
fs = j["data"]["__schema"]["queryType"]["fields"]
for f in fs:
    if any(k in f["name"].lower() for k in ("liquid", "transac", "event", "activit")):
        print("FIELD", f["name"], [(a["name"], a["type"]["name"] or (a["type"]["ofType"] or {}).get("name")) for a in f["args"]])
for tn in ("MarketTransaction", "MarketLiquidationTransaction", "Transaction", "MarketTransactionFilters", "TransactionFilters"):
    t = q('{__type(name:"%s"){name kind fields{name type{name kind ofType{name}}} inputFields{name type{name kind ofType{name kind}}} possibleTypes{name}}}' % tn)["data"]["__type"]
    if t: print("TYPE", tn, json.dumps(t)[:1500])
