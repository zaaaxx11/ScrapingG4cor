"""Rotator v0.1 - ranked pool, on fail -> next proxy automatically.

Source: pool_ranked.json (from GBP content-check, NOT gate alone).
Rule: GENUINE (GBP passers) first sorted by highest GBP, zombies unused
except --include-zombie. Every failed request (CHALLENGE/THROTTLED/EMPTY/
ERROR) -> next proxy + optional cooldown. Win -> STOP + save.

Usage:
  python3 rotator.py fetch "https://www.amazon.co.uk/s?k=creatine" [--max 5] [--out fetch_out.html]
  python3 rotator.py hunt "creatine" "whey protein" [--max 5]
  python3 rotator.py list [--sejati-only]

NEVER hardcode proxies in other code - take them from here / pool_ranked.json.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from uscraper import fetch_l3, detect, count_asin, TARGETS  # noqa: E402

RANK_FILE = os.path.join(HERE, "pool_ranked.json")
T = TARGETS["amazon-uk"]
WIN = {"SUCCESS", "PARTIAL"}


def load_pool(sejati_only: bool = True) -> list:
    """Take proxies sorted by highest GBP. GENUINE first; zombies optional."""
    try:
        rank = json.load(open(RANK_FILE))
    except (OSError, ValueError):
        return []
    if sejati_only:
        rank = [r for r in rank if r.get("ok")]
    rank.sort(key=lambda r: (-int(r.get("pound", 0)),
                             -int(r.get("asins", 0))))
    seen, out = set(), []
    for r in rank:
        px = r.get("proxy")
        if px and px not in seen:
            seen.add(px)
            out.append(px)
    return out


def fetch_rotate(url: str, proxies: list, cooldown: int = 5,
                 out: str = "fetch_out.html") -> dict:
    """Try proxies one by one until a winning signal. Return the result."""
    tried = []
    for i, px in enumerate(proxies):
        code, html, err = fetch_l3(url, px)
        sig = detect(code, html, T)
        info = {"proxy": px, "status": code, "len": len(html),
                "asin": count_asin(html), "pound": html.count(chr(163)),
                "signal": sig, "err": (err or "")[:160]}
        tried.append(info)
        print(f"[rot {i + 1}/{len(proxies)}] {px}: status={code} "
              f"len={len(html)} asin={info['asin']} GBP={info['pound']} "
              f"-> {sig}", flush=True)
        if sig in WIN:
            with open(out, "w") as fh:
                fh.write(html)
            return {"ok": True, "winner": px, "info": info,
                    "tried": len(tried), "out": out}
        if i + 1 < len(proxies) and cooldown > 0:
            time.sleep(cooldown)
    return {"ok": False, "tried": tried,
            "error": "all proxies failed (burnout? strict WAF? refresh pool)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rotator",
                                 description="ranked-proxy rotator + auto-next")
    sub = ap.add_subparsers(dest="cmd", required=True)
    li = sub.add_parser("list", help="show ranked pool")
    li.add_argument("--sejati-only", action="store_true", default=True)
    li.add_argument("--include-zombie", action="store_true")
    f = sub.add_parser("fetch", help="fetch 1 URL via rotator")
    f.add_argument("url")
    f.add_argument("--max", type=int, default=5)
    f.add_argument("--out", default="fetch_out.html")
    f.add_argument("--cooldown", type=int, default=5)
    f.add_argument("--include-zombie", action="store_true")
    h = sub.add_parser("hunt", help="hunt many keywords via rotator")
    h.add_argument("keywords", nargs="+")
    h.add_argument("--max", type=int, default=5)
    h.add_argument("--cooldown", type=int, default=5)
    a = ap.parse_args(argv)

    if a.cmd == "list":
        pool = load_pool(sejati_only=not a.include_zombie)
        print(f"ranked: {len(pool)} proxy")
        for px in pool[:20]:
            print(" ", px)
        return 0
    if a.cmd == "fetch":
        pool = load_pool(sejati_only=not a.include_zombie)[:a.max]
        if not pool:
            print("POOL EMPTY - run content-check first "
                  "(pool_ranked.json missing/empty)")
            return 2
        r = fetch_rotate(a.url, pool, cooldown=a.cooldown, out=a.out)
        if r["ok"]:
            print(f"WIN via {r['winner']} after {r['tried']}x "
                  f"tries -> {r['out']}")
            return 0
        print("FAILED:", r["error"])
        return 3
    if a.cmd == "hunt":
        pool = load_pool(sejati_only=True)[: max(a.max, 5)]
        if not pool:
            print("POOL EMPTY - content-check first")
            return 2
        wins = 0
        for kw in a.keywords:
            url = ("https://www.amazon.co.uk/s?k="
                   + kw.replace(" ", "+"))
            out = ("rot_out_"
                   + "".join(c if c.isalnum() else "_"
                              for c in kw.lower()) + ".html")
            r = fetch_rotate(url, pool, cooldown=a.cooldown, out=out)
            if r["ok"]:
                wins += 1
        print(f"SUMMARY: {wins}/{len(a.keywords)} won")
        return 0 if wins else 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
