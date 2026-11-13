import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(*a):
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)


def main(argv):
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["data_dir"] = os.path.join(HERE, cfg["data_dir"])
    if os.environ.get("MAX_RUNTIME_S"):
        cfg["max_runtime_s"] = float(os.environ["MAX_RUNTIME_S"])
    if os.environ.get("EPISODES_FILE"):
        cfg["episodes_file"] = os.environ["EPISODES_FILE"]
    cmd = argv[0] if argv else "collect"
    from . import collector, report
    if cmd == "survey":            # one discovery + one full book pass, then stop
        cfg["cold_every"] = 1
        collector.run(cfg, log, max_cycles=1)
    elif cmd == "collect":
        os.makedirs(cfg["data_dir"], exist_ok=True)
        sf = os.path.join(cfg["data_dir"], "started_at.txt")
        if not os.path.exists(sf):
            with open(sf, "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        collector.run(cfg, log)
    elif cmd == "history":
        from . import history
        history.run(cfg, log)
    elif cmd == "report":
        res = report.write(cfg)
        log(json.dumps({k: res[k] for k in ("verdict", "days", "episodes", "qualifying", "weekly_profit_usd")}))
    else:
        print("usage: python -m combo_arb [survey|collect|report]")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
