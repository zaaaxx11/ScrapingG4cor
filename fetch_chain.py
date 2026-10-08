"""Fetch chain v1 - one URL, cheapest working layer wins (all sites).

Order:
  L0  anonymous Jina (no key, $0) .... if content verifies -> STOP, done.
  L1  keyed Jina (free key) .......... if L0 fails.
  L2  keyed Jina + free proxy ........ if L1 fails (X-Proxy-Url combo).
  L3  local fetch .................... last resort: direct, then + proxy.

Content verdicts come from sites.py (markers + min length; amazon-uk
additionally needs GBP prices). HTTP status alone never counts.

Usage:
  python3 fetch_chain.py sites
  python3 fetch_chain.py fetch URL [--site amazon-uk] [--pool pool.txt]
                                   [--timeout 60] [--no-l3] [--out page.html]
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import jina_combo as JC
except ImportError as e:
    print(f"FAILED to import jina_combo.py: {e}")
    sys.exit(1)
try:
    import sites as SITES
except ImportError as e:
    print(f"FAILED to import sites.py: {e}")
    sys.exit(1)

READER = "https://r.jina.ai/"


def build_url(site_name: str, keyword: str) -> str:
    """Keyword -> fetchable URL for a site (fixed page if no {q} slot)."""
    from urllib.parse import quote_plus
    s = SITES.get(site_name or SITES.DEFAULT_SITE)
    tpl = s["search_url"]
    if "{q}" in tpl:
        return tpl.format(q=quote_plus(keyword or "creatine"))
    return tpl


def verdict_for(site_name: str, http: int, body: str) -> dict:
    """Content verdict for any site. Never trust status alone."""
    s = SITES.get(site_name or SITES.DEFAULT_SITE)
    b = body or ""
    if http == 401:
        return {"ok": False, "reason": "401 AUTH (wrong key / login required)"}
    if http == 402:
        return {"ok": False, "reason": "402 TIER (premium-only feature)"}
    if http == 422:
        return {"ok": False, "reason": "422 SELECTOR (no match in reader DOM)"}
    if http != 200:
        return {"ok": False, "reason": f"http={http} (not 200)"}
    if len(b) < s["min_len"]:
        return {"ok": False,
                "reason": f"too short ({len(b)} < {s['min_len']})"}
    if not any(m in b for m in s["ok_markers"]):
        return {"ok": False, "reason": "no product markers"}
    if not any(m in b for m in s["price_markers"]):
        return {"ok": False, "reason": "no price markers"}
    if (site_name or "").lower() == "amazon-uk":
        pounds = re.findall(r"\u00a3\s*[\d.,]+", b)
        asins = set(re.findall(r"/dp/([A-Z0-9]{10})", b))
        if "Deliver to United States" in b:
            return {"ok": False, "reason": "US locale leak (needs GB proxy)"}
        if len(pounds) < 5:
            return {"ok": False,
                    "reason": f"discovery only ({len(asins)} ASIN, "
                              f"{len(pounds)} GBP)"}
        return {"ok": True,
                "reason": f"GBP passes ({len(pounds)} prices, "
                          f"{len(asins)} ASIN)"}
    return {"ok": True, "reason": f"signals pass (len={len(b)})"}


def l0_anon(url: str, timeout: int = 60) -> tuple:
    """Anonymous Jina Reader request (no key). Return (http, body, err)."""
    try:
        p = subprocess.run(
            ["curl", "-sL", "--compressed", "--max-time", str(timeout),
             "-w", "\n%{http_code}", READER + url],
            capture_output=True, text=True, errors="replace",
            timeout=timeout + 15)
    except Exception as e:  # noqa: BLE001
        return 0, "", f"curl crash: {e}"
    out = p.stdout or ""
    try:
        code = int(out.strip().splitlines()[-1])
        body = "\n".join(out.strip().splitlines()[:-1])
    except (ValueError, IndexError):
        code, body = (200 if out else 0), out
    return code, body, ""


def _direct(url: str, timeout: int) -> tuple:
    """Local direct fetch: scrapling Fetcher first, plain curl fallback."""
    try:
        from scrapling.fetchers import Fetcher
        page = Fetcher.get(url, timeout=timeout)
        st = getattr(page, "status",
                     getattr(page, "status_code", 0)) or 0
        html = page.html_content or ""
        if st == 200 and len(html) > 1000:
            return st, html
    except Exception:  # noqa: BLE001
        pass
    try:
        p = subprocess.run(
            ["curl", "-sL", "--compressed", "-A", "Mozilla/5.0",
             "--max-time", str(timeout), "-w", "\n%{http_code}", url],
            capture_output=True, text=True, errors="replace",
            timeout=timeout + 10)
        out = p.stdout or ""
        code = int(out.strip().splitlines()[-1])
        return code, "\n".join(out.strip().splitlines()[:-1])
    except Exception:  # noqa: BLE001
        return 0, ""


def _via_proxy(url: str, proxy: str, timeout: int) -> tuple:
    """Local StealthyFetcher fetch through one proxy (needs chromium)."""
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        return 0, "scrapling not installed"
    try:
        page = StealthyFetcher.fetch(
            url, headless=True, network_idle=True, proxy=proxy,
            block_webrtc=True, locale="en-GB",
            timezone_id="Europe/London", timeout=timeout * 1000)
        st = getattr(page, "status", 0) or 0
        return st, page.html_content or ""
    except Exception as e:  # noqa: BLE001
        return 0, str(e)[:160]


def l3_fetch(url: str, proxies: list, timeout: int = 60) -> dict:
    """L3 last resort: direct first, then each proxy. Return result dict."""
    code, html = _direct(url, timeout)
    v = verdict_for("generic-direct", code, html)
    # direct verdict uses the caller's site markers; proxy loop below
    # re-checks per site in fetch(). Here: length + price-shape sanity.
    if code == 200 and len(html or "") > 5000:
        return {"ok": "maybe", "via": "L3-direct", "http": code,
                "len": len(html), "reason": "direct 200, verify vs site",
                "body": html}
    for px in (proxies or []):
        c2, h2 = _via_proxy(url, px, timeout)
        if c2 == 200 and len(h2 or "") > 5000:
            return {"ok": "maybe", "via": f"L3-proxy {px}", "http": c2,
                    "len": len(h2), "reason": "proxy 200, verify vs site",
                    "body": h2}
    return {"ok": False, "via": None, "http": code,
            "len": len(html or ""),
            "reason": "L3 empty (direct + proxies, no usable page)"}


def _read_pool(pool_file: str, max_n: int) -> list:
    try:
        with open(pool_file, errors="ignore") as fh:
            return [l.strip() for l in fh
                    if l.strip() and not l.startswith("#")][:max_n]
    except (OSError, IOError):
        return []


def fetch(url: str, site: str = "amazon-uk", key: str | None = None,
          proxies: list | None = None, pool_file: str | None = None,
          timeout: int = 60, use_l3: bool = True,
          max_combo: int = 5) -> dict:
    """Run L0 -> L1 -> L2 -> L3. First content-verified layer wins."""
    site = (site or "amazon-uk").lower()
    key = key or JC.jina_key()
    trace = []

    # L0: anonymous Jina
    c0, b0, e0 = l0_anon(url, timeout)
    v0 = verdict_for(site, c0, b0)
    trace.append({"layer": "L0-anon-jina", "http": c0,
                  "len": len(b0), "ok": v0["ok"], "reason": v0["reason"]})
    if v0["ok"]:
        return {"ok": True, "via": "L0-anon-jina", "site": site,
                "http": c0, "len": len(b0), "reason": v0["reason"],
                "body": b0, "trace": trace}

    # L1: keyed Jina
    if key:
        r1 = JC.jina_fetch(url, key=key, timeout=timeout)
        v1 = verdict_for(site, r1.get("http", 0), r1.get("body", ""))
        trace.append({"layer": "L1-keyed-jina", "http": r1.get("http"),
                      "len": r1.get("len", 0), "ok": v1["ok"],
                      "reason": v1["reason"]})
        if v1["ok"]:
            return {"ok": True, "via": "L1-keyed-jina", "site": site,
                    "http": r1.get("http"), "len": r1.get("len", 0),
                    "reason": v1["reason"], "body": r1.get("body", ""),
                    "trace": trace}
    else:
        trace.append({"layer": "L1-keyed-jina", "http": 0, "len": 0,
                      "ok": False,
                      "reason": "no key (run get_key.py to add one)"})

    # L2: keyed Jina + free proxy combo
    pool = list(proxies or [])
    if pool_file and not pool:
        pool = _read_pool(pool_file, 50)
    if key and pool:
        alive = []
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=20) as ex:
            res = list(ex.map(lambda px: JC.gate_check(px, url),
                              pool[:50]))
        alive = [px for px, code, ok in res if ok]
        trace.append({"layer": "L2-gate", "http": 0, "len": 0,
                      "ok": bool(alive),
                      "reason": f"pool={min(len(pool), 50)} "
                                f"gate-alive={len(alive)}"})
        for px in alive[:max_combo]:
            r2 = JC.jina_fetch(url, proxy=px, key=key, timeout=timeout)
            v2 = verdict_for(site, r2.get("http", 0), r2.get("body", ""))
            trace.append({"layer": f"L2-combo {px}",
                          "http": r2.get("http"), "len": r2.get("len", 0),
                          "ok": v2["ok"], "reason": v2["reason"]})
            if v2["ok"]:
                return {"ok": True, "via": f"L2-combo {px}", "site": site,
                        "http": r2.get("http"), "len": r2.get("len", 0),
                        "reason": v2["reason"],
                        "body": r2.get("body", ""), "trace": trace}
    elif not key:
        trace.append({"layer": "L2-combo", "http": 0, "len": 0,
                      "ok": False, "reason": "skipped (needs key)"})
    else:
        trace.append({"layer": "L2-combo", "http": 0, "len": 0,
                      "ok": False, "reason": "skipped (empty pool)"})

    # L3: local fetch, verified against the same site verdict
    if use_l3:
        lr = l3_fetch(url, pool, timeout)
        if lr.get("ok") == "maybe":
            v3 = verdict_for(site, lr["http"], lr.get("body", ""))
            trace.append({"layer": lr["via"], "http": lr["http"],
                          "len": lr["len"], "ok": v3["ok"],
                          "reason": v3["reason"]})
            if v3["ok"]:
                return {"ok": True, "via": lr["via"], "site": site,
                        "http": lr["http"], "len": lr["len"],
                        "reason": v3["reason"], "body": lr.get("body", ""),
                        "trace": trace}
        else:
            trace.append({"layer": "L3-local", "http": lr.get("http", 0),
                          "len": lr.get("len", 0), "ok": False,
                          "reason": lr.get("reason", "")})
    return {"ok": False, "via": None, "site": site, "trace": trace,
            "error": "all layers empty (refresh pool / retry later)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_chain",
                                 description="L0 anon-Jina -> L1 key -> "
                                             "L2 key+proxy -> L3 local")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sites", help="list supported sites + methods "
                                 "(builtin + yours)")
    g = sub.add_parser("my-sites", help="list YOUR targets only")
    a = sub.add_parser("add-site", help="register YOUR target site")
    a.add_argument("key", help="short name, e.g. my-shop")
    a.add_argument("url", help="search or catalog URL "
                               "({q} = keyword slot, optional)")
    a.add_argument("--label", default=None)
    a.add_argument("--kind", default="generic",
                   help="waf|search-page|shopify|product-pages|generic")
    a.add_argument("--price", default="£",
                   help="comma-separated price markers")
    a.add_argument("--ok", default=None,
                   help="comma-separated product markers "
                        "(default: hostname)")
    a.add_argument("--min-len", type=int, default=3000)
    r = sub.add_parser("remove-site", help="delete YOUR target site")
    r.add_argument("key")
    f = sub.add_parser("fetch", help="fetch 1 URL through the chain")
    f.add_argument("url")
    f.add_argument("--site", default="amazon-uk",
                   help="site key (see: fetch_chain.py sites)")
    f.add_argument("--pool", default=None,
                   help="proxy pool file for L2/L3")
    f.add_argument("--timeout", type=int, default=60)
    f.add_argument("--no-l3", action="store_true",
                   help="stop after Jina layers")
    f.add_argument("--out", default=None, help="save winner body to file")
    a = ap.parse_args(argv)

    if a.cmd == "sites":
        for name in SITES.names():
            s = SITES.get(name)
            tag = " [yours]" if name in SITES.user_sites() else ""
            print(f"{name:26} {s.get('label', name):28} "
                  f"{s.get('method', '')[:70]}{tag}")
        return 0
    if a.cmd == "my-sites":
        yours = SITES.user_sites()
        if not yours:
            print("no user targets yet (see: add-site --help, "
                  "targets.example.json)")
            return 0
        for name, s in yours.items():
            print(f"{name:26} {s.get('label', name):28} "
                  f"{s.get('search_url', '')[:80]}")
        return 0
    if a.cmd == "add-site":
        from urllib.parse import urlparse
        host = urlparse(a.url).hostname or ""
        ok = [m.strip() for m in (a.ok.split(",") if a.ok else [host])
              if m.strip()]
        price = [m.strip() for m in a.price.split(",") if m.strip()]
        entry = {"label": a.label or a.key, "kind": a.kind,
                 "search_url": a.url,
                 "method": f"user target ({a.kind})",
                 "price_markers": price, "ok_markers": ok,
                 "min_len": a.min_len, "notes": "user-defined target"}
        try:
            path = SITES.add_user_site(a.key, entry)
        except ValueError as e:
            print(f"REJECTED: {e}")
            return 2
        print(f"saved '{a.key}' -> {path} "
              f"(fetch with: --site {a.key})")
        return 0
    if a.cmd == "remove-site":
        if SITES.remove_user_site(a.key):
            print(f"removed '{a.key}'")
            return 0
        print(f"not found (yours: {list(SITES.user_sites()) or 'none'})")
        return 2
    if a.cmd == "fetch":
        r = fetch(a.url, site=a.site, pool_file=a.pool,
                  timeout=a.timeout, use_l3=not a.no_l3)
        show = {k: r.get(k) for k in
                ("ok", "via", "site", "http", "len", "reason", "error")}
        show["trace"] = r.get("trace", [])
        print(json.dumps(show, indent=1, ensure_ascii=False))
        if r.get("ok"):
            path = a.out or f"chain_{a.site}.md"
            with open(path, "w") as fh:
                fh.write(r.get("body", ""))
            print(f"-> {path}")
            return 0
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
