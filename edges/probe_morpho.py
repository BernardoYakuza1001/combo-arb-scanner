import json, requests
API = "https://blue-api.morpho.org/graphql"
def q(s): return requests.post(API, json={"query": s}, timeout=60).json()
print("ENUM", q('{__type(name:"MarketTransactionType"){enumValues{name}}}')["data"]["__type"])
pt = q('{__type(name:"MarketTransactionData"){kind possibleTypes{name}}}')["data"]["__type"]; print("UNION", pt)
for p in (pt.get("possibleTypes") or []):
    if "iquid" in p["name"]:
        print("LIQTYPE", json.dumps(q('{__type(name:"%s"){fields{name type{name kind ofType{name}}}}}' % p["name"])["data"]["__type"]))
print("MARKET", [f["name"] for f in q('{__type(name:"Market"){fields{name}}}')["data"]["__type"]["fields"]][:60])
print("CHAIN", [f["name"] for f in q('{__type(name:"Chain"){fields{name}}}')["data"]["__type"]["fields"]])
