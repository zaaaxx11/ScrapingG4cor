"""Universal Scraper v0.1 - ladder L0/L1/L3/L5 + detector + proxy validator + GBP-guard.

One file, new target = new TARGETS dict, no rewrite.
Proven patterns from the amazon-uk-scraper skill (2026-10-06).

Usage:
  python3 uscraper.py selftest                       # offline detector unit test, no network
  python3 uscraper.py probe "https://www.amazon.co.uk/s?k=creatine"
  python3 uscraper.py validate proxies.txt --max 20
  python3 uscraper.py fetch "https://www.amazon.co.uk/s?k=creatine" --proxy http://IP:PORT
  python3 uscraper.py bd-search "creatine"            # needs ~/.brightdata_key / BRIGHTDATA_KEY
  python3 uscraper.py bd-dp B00T7L20AQ

Rule: cheap first (L0->L1->L3), expensive last (L5). Success = content, not status.
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys

SUCCESS = "SUCCESS"
PARTIAL = "PARTIAL"

TARGETS = {
    "amazon-uk": {
        "name": "amazon-uk",
        "CHALLENGE_MARKERS": ["gokuprops", "awswaf", "challenge.js", "token.awswaf.com"],
        "THROTTLE_MARKERS": ["amazonsorry", "request blocked", "captcha-delivery",
                             "to discuss automated access to amazon data"],
        "CONSENT_MARKERS": ["cookie preferences", "select your cookie"],
        "DATA_MARKER": "data-asin",
        "PRICE_MARKERS": ["a-price-whole", "\u00a3", "&pound;", "a-offscreen"],
        "GBP_MIN_CARDS": 5,  # GBP-guard: minimum GBP-priced cards, prevents locale-leak
    },
    "generic": {
        "name": "generic",
        "CHALLENGE_MARKERS": ["challenge-platform", "cf-challenge", "turnstile",
                              "gokuprops", "awswaf"],
        "THROTTLE_MARKERS": ["rate limit", "too many requests", "service unavailable"],
        "CONSENT_MARKERS": ["cookie"],
        "DATA_MARKER": "data-asin",
        "PRICE_MARKERS": ["\u00a3", "$", "\u20ac", "price"],
        "GBP_MIN_CARDS": 0,
    },
}


def _hits(low: str, markers) -> bool:
    return any(m.lower() in low for m in markers)


def detect(status: int, html: str, target: dict = None) -> str:
    """Classify fetch results. Only SUCCESS/PARTIAL may be parsed."""
    t = target or TARGETS["amazon-uk"]
    low = (html or "").lower()
    n_data = (html or "").count(str(t.get("DATA_MARKER", "data-asin")))
    if _hits(low, list(t.get("CHALLENGE_MARKERS", []))):
        return "CHALLENGE"
    if status in (429, 503) or _hits(low, list(t.get("THROTTLE_MARKERS", []))):
        return "THROTTLED"
    if status == 200 and n_data == 0:
        if _hits(low, list(t.get("CONSENT_MARKERS", []))):
            return "CONSENT_WALL"
        return "EMPTY"
    if status == 200 and n_data > 0:
        pms = list(t.get("PRICE_MARKERS", []))
        if any(pm in (html or "") or pm in low for pm in pms):
            # GBP-guard: for amazon-uk, count GBP-priced cards
            need = int(t.get("GBP_MIN_CARDS", 0) or 0)
            if need and t.get("name") == "amazon-uk":
                cards = (html or "").count("\u00a3")
                if cards < need:
                    return PARTIAL  # data present but too few GBP prices = variant/locale-leak
            return SUCCESS
        return PARTIAL
    if status in (202,):
        return "CHALLENGE"
    return "UNKNOWN"


def count_asin(html: str) -> int:
    return (html or "").count('data-asin="') or (html or "").count("data-asin")


# --- L0: plain curl (signal probe, expect a block) ---
def fetch_l0(url: str, timeout: int = 20):
    p = subprocess.run(
        ["curl", "-s", "--compressed", "-o", "-", "-w", "\n%{http_code}",
         "-A", "Mozilla/5.0", "--max-time", str(timeout), url],
        capture_output=True, text=True, errors="replace",
        timeout=timeout + 10)
    out = p.stdout
    code = 0
    try:
        code = int(out.strip().splitlines()[-1])
        html = "\n".join(out.strip().splitlines()[:-1])
    except (ValueError, IndexError):
        html = out
    return code, html


# Scrapling Response has NO .status_code - use .status (documented quirk)!
def _page_status(page) -> int:
    for attr in ("status", "status_code"):
        try:
            v = getattr(page, attr, None)
            if isinstance(v, int):
                return v
        except Exception:
            pass
    return -1


# --- L1: Fetcher impersonate-chrome (cheap baseline) ---
def fetch_l1(url: str, timeout: int = 30):
    try:
        from scrapling.fetchers import Fetcher
    except ImportError:
        return -1, "", "scrapling not installed: pip install 'scrapling[fetchers]'"
    try:
        page = Fetcher.get(url, impersonate="chrome", stealthy_headers=True,
                            timeout=timeout)
        return _page_status(page), page.html_content, ""
    except Exception as e:  # noqa: BLE001
        return -1, "", f"L1 error: {e}"


# --- L3: StealthyFetcher + healthy proxy (main free weapon). timeout = MILLISECONDS! ---
def fetch_l3(url: str, proxy: str, timeout_ms: int = 60000):
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        return -1, "", "scrapling not installed: pip install 'scrapling[fetchers]'"
    try:
        page = StealthyFetcher.fetch(
            url, headless=True, network_idle=True, proxy=proxy,
            block_webrtc=True, locale="en-GB", timezone_id="Europe/London",
            timeout=timeout_ms,
            wait_selector='[data-component-type="s-search-result"]')
        return _page_status(page), page.html_content, ""
    except Exception as e:  # noqa: BLE001
        return -1, "", f"L3 error: {e}"


# --- proxy validator: fast curl gate, alive = code 200/202/301/302/403/503 ---
ALIVE = {200, 202, 301, 302, 403, 503}


def _check_one(args):
    proxy, url, timeout = args
    try:
        p = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-x", proxy, "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 10)
        code = int(p.stdout.strip() or 0)
        return proxy, code, code in ALIVE
    except Exception:  # noqa: BLE001
        return proxy, 0, False


def validate_proxies(proxies, url: str, timeout: int = 10, workers: int = 20):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_check_one, [(px, url, timeout) for px in proxies]))


# --- L5: Bright Data datasets API (key from env/file, NEVER hardcode) ---
def bd_key() -> str:
    env = os.environ.get("BRIGHTDATA_KEY", "").strip()
    if env:
        return env
    return open(os.path.expanduser("~/.brightdata_key")).read().strip()


def _split_response(raw: str) -> tuple:
    """Split curl -D headers from body. Return (http_code, headers_dict, body)."""
    code, hdrs, body = 0, {}, raw
    if raw.startswith("HTTP/"):
        head, _, rest = raw.partition("\r\n\r\n")
        if not rest:
            head, _, rest = raw.partition("\n\n")
        for line in head.splitlines():
            if line.startswith("HTTP/") and not code:
                try:
                    code = int(line.split()[1])
                except (IndexError, ValueError):
                    code = 0
            elif ":" in line:
                k, _, v = line.partition(":")
                hdrs[k.strip().lower()] = v.strip()
        body = rest.strip()
    return code, hdrs, body


def bd_call(dataset: str, payload: dict, timeout: int = 120) -> tuple:
    """POST datasets API. Return (http_code, headers, body). debug=true on."""
    import time
    key = bd_key()
    code, hdrs, body = 0, {}, ""
    for attempt in range(2):
        p = subprocess.run(
            ["curl", "-s", "-D", "-", "--max-time", str(timeout),
             "-H", f"Authorization: Bearer {key}",
             "-H", "Content-Type: application/json",
             "-d", json.dumps(payload),
             f"https://api.brightdata.com/datasets/v3/scrape"
             f"?dataset_id={dataset}&notify=false&include_errors=true&debug=true"],
            capture_output=True, text=True, errors="replace",
            timeout=timeout + 10)
        code, hdrs, body = _split_response(p.stdout or "")
        if body == "Customer is not active" and attempt == 0:
            time.sleep(5)
            continue
        return code, hdrs, body
    return code, hdrs, body


def norm_currency(c: str) -> str:
    return {"GPB": "GBP"}.get((c or "").upper(), c or "")


def bd_search(keyword: str, pages: int = 1,
              domain: str = "https://www.amazon.co.uk"):
    code, hdrs, body = bd_call("gd_lwdb4vjm1ehb499uxs",
                   {"input": [{"keyword": keyword, "url": domain,
                               "pages_to_search": pages}],
                    "limit_per_input": None})
    dbg = hdrs.get("x-brd-debug", "")
    print(f"[bd] http={code} debug={dbg[:160]}", flush=True)
    if body == "Customer is not active":
        return None, f"LIMIT_EMPTY (http={code}): full STOP, L3 fallback (don't burn calls)"
    recs = [json.loads(l) for l in body.splitlines() if l.strip().startswith("{")]
    for r in recs:
        r["currency"] = norm_currency(r.get("currency"))
    if recs and isinstance(recs[0], dict):
        recs[0].setdefault("_meta", {"_http": code, "_debug": dbg})
    return recs, ""


def ladder(url: str, target_name: str = "amazon-uk", proxy: str = None):
    """L0 -> L1 -> L3. Return (level, status, signal, html, note)."""
    t = TARGETS.get(target_name, TARGETS["amazon-uk"])
    code, html = fetch_l0(url)
    sig = detect(code, html, t)
    if sig in (SUCCESS, PARTIAL):
        return "L0", code, sig, html, f"asin={count_asin(html)}"
    code1, html1, err1 = fetch_l1(url)
    if err1:
        return "L1", code1, "ERROR", html1, err1
    sig1 = detect(code1, html1, t)
    if sig1 in (SUCCESS, PARTIAL) or not proxy:
        lvl = "L1"
        return lvl, code1, sig1, html1, f"asin={count_asin(html1)}"
    code3, html3, err3 = fetch_l3(url, proxy)
    if err3:
        return "L3", code3, "ERROR", html3, err3
    return "L3", code3, detect(code3, html3, t), html3, f"asin={count_asin(html3)}"


def cmd_selftest():
    t = TARGETS["amazon-uk"]
    cases = [
        (202, "<html>gokuProps AwsWafIntegration</html>", "CHALLENGE"),
        (503, "<html>amazonsorry</html>", "THROTTLED"),
        (200, "<html>Select your cookie preferences</html>", "CONSENT_WALL"),
        (200, "<html>no data here</html>", "EMPTY"),
        (200, '<div data-asin="B1">x</div>' * 6 + "<span>\u00a39.99</span>" * 6, "SUCCESS"),
        (200, '<div data-asin="B1">x</div>' * 6 + "<span>N sizes</span>", "PARTIAL"),
    ]
    ok = 0
    for status, html, want in cases:
        got = detect(status, html, t)
        mark = "OK " if got == want else "FAIL"
        if got == want:
            ok += 1
        print(f"{mark} status={status} want={want} got={got}")
    print(f"selftest: {ok}/{len(cases)} PASS")
    return 0 if ok == len(cases) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="uscraper",
                                 description="Universal scraper ladder L0/L1/L3/L5")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest", help="offline detector unit test")
    p = sub.add_parser("probe", help="L0 curl probe, 1 request (signal classification)")
    p.add_argument("url")
    v = sub.add_parser("validate", help="validate proxy list via curl gate")
    v.add_argument("file", help="file with http://IP:PORT per line")
    v.add_argument("--max", type=int, default=40)
    v.add_argument("--url", default="https://www.amazon.co.uk/s?k=creatine")
    f = sub.add_parser("fetch", help="ladder L0->L1->L3")
    f.add_argument("url")
    f.add_argument("--proxy", default=None)
    f.add_argument("--target", default="amazon-uk")
    s = sub.add_parser("bd-search", help="L5 Bright Data search (needs key+credit)")
    s.add_argument("keyword", nargs="?", default="creatine")
    s.add_argument("pages", nargs="?", type=int, default=1)
    d = sub.add_parser("bd-dp", help="L5 Bright Data product DP (needs key+credit)")
    d.add_argument("asin")
    a = ap.parse_args(argv)

    if a.cmd == "selftest":
        return cmd_selftest()
    if a.cmd == "probe":
        code, html = fetch_l0(a.url)
        sig = detect(code, html)
        print(f"L0 probe: status={code} len={len(html)} "
              f"asin={count_asin(html)} signal={sig}")
        return 0
    if a.cmd == "validate":
        lines = [l.strip() for l in open(a.file) if l.strip()][:a.max]
        res = validate_proxies(lines, a.url)
        alive = [(px, c) for px, c, ok in res if ok]
        print(f"checked={len(res)} alive={len(alive)}")
        for px, c in alive:
            print(f"ALIVE {c} {px}")
        json.dump([{"proxy": px, "code": c} for px, c in alive],
                  open("live_proxies.json", "w"), indent=1)
        print("-> live_proxies.json")
        return 0
    if a.cmd == "fetch":
        lvl, code, sig, html, note = ladder(a.url, a.target, a.proxy)
        print(f"{lvl}: status={code} len={len(html)} signal={sig} {note}")
        if sig in (SUCCESS, PARTIAL):
            open("fetch_out.html", "w").write(html)
            print("-> fetch_out.html")
        return 0 if sig in (SUCCESS, PARTIAL) else 2
    if a.cmd == "bd-search":
        recs, err = bd_search(a.keyword, a.pages)
        if err:
            print(err)
            return 3
        json.dump(recs, open(f"bd_search_{a.keyword}.json", "w"), indent=1)
        priced = sum(1 for r in recs if r.get("final_price"))
        print(f"search '{a.keyword}': {len(recs)} rec, {priced} priced")
        return 0
    if a.cmd == "bd-dp":
        from urllib.parse import quote  # noqa: F401  (keep stdlib-only)
        url = (a.asin if a.asin.startswith("http")
               else f"https://www.amazon.co.uk/dp/{a.asin}")
        code, hdrs, body = bd_call("gd_l7q7dkf244hwjntr0",
                       {"input": [{"url": url, "zipcode": "SW1A 1AA"}],
                        "limit_per_input": None})
        dbg = hdrs.get("x-brd-debug", "")
        print(f"[bd] http={code} debug={dbg[:160]}", flush=True)
        if body == "Customer is not active":
            print(f"LIMIT_EMPTY (http={code}): full STOP, L3 fallback")
            return 3
        dd = json.loads(body.splitlines()[0])
        dd["currency"] = norm_currency(dd.get("currency"))
        dd["_http"] = code
        dd["_debug"] = dbg
        json.dump(dd, open(f"bd_dp_{a.asin}.json", "w"), indent=1)
        print(f"dp {a.asin}: final_price={dd.get('final_price')} "
              f"{dd.get('currency')} seller={dd.get('seller_name')}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
