"""Jina combo v0.1 - auto API key + stage materials -> execute.

Logic: JINA_KEY automatic from env/file (no manual -H),
stage materials (key + proxy pool + URL) -> run combo
(Jina render + fresh GB X-Proxy-Url + x-no-cache + timeout).

Key: JINA_API_KEY / JINA_KEY env first, then ~/.jina_key (chmod 600).
NEVER hardcode keys. Store via: python3 jina_combo.py set-key <KEY>
(spaces in keys auto-stripped).

Usage:
  python3 jina_combo.py status
  python3 jina_combo.py set-key <KEY>
  python3 jina_combo.py fetch "https://www.amazon.co.uk/s?k=creatine" [--proxy http://IP:PORT] [--selector .a-price]
  python3 jina_combo.py combo-search "creatine" [--pool pool_gb_fresh.txt] [--max 10]

Proven B7 (2026-10-07): 344KB / 52 ASIN / 287 GBP via fresh GB proxy + key + fresh + timeout 60.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys

KEY_FILE = os.path.expanduser("~/.jina_key")
READER = "https://r.jina.ai/"
GBP_MIN = 5  # GBP-guard: minimum GBP-priced cards to beat locale-leak


def jina_key() -> str:
    """Get Jina key: JINA_API_KEY/JINA_KEY env first, then ~/.jina_key.

    Spaces stripped automatically (pasted spaced keys still work).
    Return '' when missing (caller reports it, never crash silently).
    """
    for env in ("JINA_API_KEY", "JINA_KEY"):
        v = os.environ.get(env, "").replace(" ", "").strip()
        if v:
            return v
    try:
        with open(KEY_FILE) as f:
            return f.read().replace(" ", "").strip()
    except (OSError, IOError):
        return ""


def key_info(k: str) -> str:
    """Key status without leaking the value: length + first 4 chars only."""
    if not k:
        return "EMPTY (set via: jina_combo.py set-key <KEY> / env JINA_API_KEY)"
    return f"present ({len(k)} char, prefix {k[:4]}..., file {KEY_FILE})"


def cmd_set_key(raw: str) -> int:
    """Store key in ~/.jina_key chmod 600. Spaces stripped automatically."""
    k = (raw or "").replace(" ", "").strip()
    if not k.startswith("jina_") or len(k) < 20:
        print("KEY REJECTED: odd format (must start with jina_ and have sane length)")
        return 2
    with open(KEY_FILE, "w") as f:
        f.write(k + "\n")
    os.chmod(KEY_FILE, 0o600)
    print(f"key stored -> {KEY_FILE} (600, {len(k)} char)")
    return 0


def jina_fetch(url: str, proxy: str = None, selector: str = None,
               timeout: int = 60, no_cache: bool = True,
               key: str = None) -> dict:
    """1 Jina Reader request. Return result dict + content stats.

    Key automatic from jina_key() if omitted. Without a key = 20 RPM
    anonymous (X-Proxy-Url REJECTED with 401 - key required).
    """
    k = key or jina_key()
    if not k:
        return {"ok": False, "http": 0, "error": "EMPTY KEY",
                "note": "run set-key first / export JINA_API_KEY"}
    target = url if url.startswith("http") else "https://" + url
    cmd = ["curl", "-sL", "-D", "-", "--compressed",
           "--max-time", str(timeout + 30),
           "-H", f"Authorization: Bearer {k}",
           "-H", "X-Timeout: " + str(timeout)]
    if no_cache:
        cmd += ["-H", "x-no-cache: true"]
    if proxy:
        cmd += ["-H", f"X-Proxy-Url: {proxy}"]
    if selector:
        cmd += ["-H", f"X-Target-Selector: {selector}"]
    cmd.append(READER + target)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           errors="replace", timeout=timeout + 40)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "http": 0, "error": f"curl crash: {e}"}
    raw = p.stdout or ""
    code, body = _split(raw)
    if code == 401:
        return {"ok": False, "http": 401, "error": "401 AUTH",
                "note": "wrong key / feature needs a higher tier (see body)",
                "body_head": body[:300]}
    if code == 402:
        return {"ok": False, "http": 402, "error": "402 TIER",
                "note": "premium-only feature (e.g. X-Proxy residential)",
                "body_head": body[:300]}
    if code == 422:
        return {"ok": False, "http": 422, "error": "422 SELECTOR",
                "note": "selector does not match the Jina DOM",
                "body_head": body[:300]}
    stats = _stats(body)
    stats.update({"ok": True, "http": code, "proxy": proxy or "(no proxy)",
                  "selector": selector or "(default)", "len": len(body)})
    stats["body"] = body
    return stats


def _split(raw: str) -> tuple:
    """Split curl -D headers from body. Return (http_code, body).

    Follow-redirect (-L) can yield multiple header blocks - take the LAST one.
    """
    code, body = 0, raw
    blocks = re.split(r"\r?\n\r?\n", raw)
    # find the last valid status line
    for i in range(len(blocks) - 1, -1, -1):
        head = blocks[i]
        if re.match(r"HTTP/\d", head.strip()):
            try:
                code = int(head.strip().split()[1])
            except (IndexError, ValueError):
                code = 0
            body = "\r\n\r\n".join(blocks[i + 1:]).strip()
            break
    else:
        if raw.startswith("HTTP/"):
            try:
                code = int(raw.split()[1])
            except (IndexError, ValueError):
                code = 0
    return code, body


def _stats(body: str) -> dict:
    """Amazon content stats: ASIN, GBP, USD, locale, cache, sorry."""
    asins = set(re.findall(r"/dp/([A-Z0-9]{10})", body or ""))
    pounds = re.findall(r"\u00a3\s*[\d.,]+", body or "")
    usds = re.findall(r"USD\s*[\d.,]+", body or "")
    return {
        "asins": len(asins),
        "asin_sample": sorted(asins)[:5],
        "pound": len(pounds),
        "pound_sample": pounds[:8],
        "usd": len(usds),
        "deliver_us": "Deliver to United States" in (body or ""),
        "cached": "cached snapshot" in (body or ""),
        "sorry": (body or "").lower().count("sorry"),
        "ratings": len(re.findall(r"\d\.\d out of 5", body or "")),
        "gbp_pass": len(pounds) >= GBP_MIN,
    }


def gate_check(proxy: str, url: str = "https://www.amazon.co.uk/s?k=creatine",
               timeout: int = 10) -> tuple:
    """Fast curl gate check via proxy. Return (proxy, code, alive)."""
    try:
        p = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-x", proxy, "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 10)
        code = int((p.stdout or "").strip() or 0)
        return proxy, code, code in {200, 202, 301, 302, 403, 503}
    except Exception:  # noqa: BLE001
        return proxy, 0, False


def stage_materials(pool_file: str, max_n: int, url: str) -> tuple:
    """Stage materials: key + pool -> gate-check -> live list.

    Return (key, alive_list, note). Hemat: gate-check direct ($0,
    no Jina quota spent) BEFORE burning proxied Jina requests.
    """
    k = jina_key()
    if not k:
        return "", [], "EMPTY KEY - run set-key first"
    try:
        lines = [l.strip() for l in open(pool_file)
                 if l.strip()][:max_n]
    except (OSError, IOError):
        return k, [], f"POOL MISSING: {pool_file}"
    if not lines:
        return k, [], "POOL EMPTY"
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        res = list(ex.map(lambda px: gate_check(px, url),
                          lines))
    alive = [px for px, code, ok in res if ok]
    note = f"pool={len(lines)} gate-alive={len(alive)}"
    return k, alive, note


def combo_search(keyword: str, pool_file: str = "pool_gb_fresh.txt",
                 max_n: int = 10) -> dict:
    """Stage materials -> execute: Jina + live proxies until GBP passes.

    Cheapest order: (1) Jina with no proxy first (free, sometimes enough
    for discovery), (2) then live proxies one by one.
    STOP at first GBP >= GBP_MIN. Return summary + winner.
    """
    url = f"https://www.amazon.co.uk/s?k={keyword}"
    k, alive, note = stage_materials(pool_file, max_n, url)
    print(f"[materials] key={key_info(k)} | {note}", flush=True)
    if not k:
        return {"ok": False, "error": "EMPTY KEY"}
    # step 1: no proxy (cheap discovery)
    r0 = jina_fetch(url, proxy=None, key=k)
    print(f"[probe] no-proxy: http={r0.get('http')} len={r0.get('len', 0)} "
          f"asin={r0.get('asins', 0)} GBP={r0.get('pound', 0)} "
          f"US={r0.get('deliver_us')}", flush=True)
    if r0.get("gbp_pass"):
        r0["winner"] = "(no proxy)"
        return {"ok": True, "result": r0, "note": note}
    # step 2: live proxies one by one
    for px in alive:
        r = jina_fetch(url, proxy=px, key=k)
        print(f"[combo] {px}: http={r.get('http')} "
              f"len={r.get('len', 0)} asin={r.get('asins', 0)} "
              f"GBP={r.get('pound', 0)} US={r.get('deliver_us')} "
              f"{r.get('error', '')}", flush=True)
        if r.get("gbp_pass"):
            r["winner"] = px
            return {"ok": True, "result": r, "note": note}
    return {"ok": False, "error": "GBP passed on no candidate",
            "note": note, "probe": {x: r0.get(x) for x in
                                    ("http", "asins", "pound")}}


def cmd_status() -> int:
    k = jina_key()
    print(f"key: {key_info(k)}")
    for f in ("pool_gb_fresh.txt", "pool_fresh.txt", "live_proxies.json"):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
        if os.path.exists(p):
            n = sum(1 for _ in open(p, errors="ignore"))
            print(f"pool: {f} ({n} lines)")
        else:
            print(f"pool: {f} (MISSING)")
    print(f"GBP-guard: >= {GBP_MIN} GBP-priced cards")
    return 0 if k else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jina_combo",
                                 description="Jina auto-key + stage materials -> execute")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sk = sub.add_parser("set-key", help="store API key in ~/.jina_key")
    sk.add_argument("key", help="jina_* key (spaces auto-stripped)")
    sub.add_parser("status", help="check key + pools (no network)")
    f = sub.add_parser("fetch", help="1 Jina request (auto-key)")
    f.add_argument("url")
    f.add_argument("--proxy", default=None)
    f.add_argument("--selector", default=None)
    f.add_argument("--timeout", type=int, default=60)
    c = sub.add_parser("combo-search",
                       help="stage materials -> execute until GBP passes")
    c.add_argument("keyword", nargs="?", default="creatine")
    c.add_argument("--pool", default="pool_gb_fresh.txt")
    c.add_argument("--max", type=int, default=10)
    c.add_argument("--out", default="jina_combo_out.md",
                   help="save winner markdown")
    a = ap.parse_args(argv)

    if a.cmd == "set-key":
        return cmd_set_key(a.key)
    if a.cmd == "status":
        return cmd_status()
    if a.cmd == "fetch":
        r = jina_fetch(a.url, proxy=a.proxy, selector=a.selector,
                       timeout=a.timeout)
        # NEVER print key / full body when large - summary + proof
        show = {kk: r.get(kk) for kk in
                ("ok", "http", "len", "asins", "pound", "usd",
                 "deliver_us", "cached", "sorry", "ratings", "gbp_pass",
                 "proxy", "selector", "error", "note", "body_head")}
        show["asin_sample"] = r.get("asin_sample", [])[:5]
        show["pound_sample"] = r.get("pound_sample", [])[:8]
        print(json.dumps(show, indent=1, ensure_ascii=False))
        if r.get("ok") and r.get("body"):
            open("jina_fetch_out.md", "w").write(r["body"])
            print("-> jina_fetch_out.md")
        return 0 if r.get("ok") else 2
    if a.cmd == "combo-search":
        out = combo_search(a.keyword, a.pool, a.max)
        if out.get("ok"):
            r = out["result"]
            print(f"\nWIN via {r.get('winner')}: "
                  f"len={r.get('len')} asin={r.get('asins')} "
                  f"GBP={r.get('pound')} ratings={r.get('ratings')} "
                  f"US={r.get('deliver_us')}", flush=True)
            open(a.out, "w").write(r.get("body", ""))
            print(f"-> {a.out}")
            return 0
        print(f"FAILED: {out.get('error')} | {out.get('note')}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
