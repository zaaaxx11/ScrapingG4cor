"""Hunt workflow v0.3 - big batches + refresh on every hunt.

Flow:
  1. PREP  - prepare all env (jina key, fresh multi-source proxy pools)
  2. CHECK - check all env (jina? proxies?) -> Good/Bad verdict PER COMPONENT
  3. FIX - Bad proxy -> refresh + find proxies (big batches) + CONTENT-CHECK.
             alive = gate 202 AND passes GBP-guard via Jina (not gate alone!).
             Bad jina -> provide a new key (keys cannot be auto-generated!)
  4. HUNT - Good -> Jina combo per keyword; if Jina fails everywhere -> L3 FALLBACK
             (local StealthyFetcher + ranked pool, auto-rotate) -> output.

Usage:
  python3 hunt_workflow.py prep [--big]
  python3 hunt_workflow.py check
  python3 hunt_workflow.py hunt "creatine" "whey protein" [--max 50] [--want 3]
  python3 hunt_workflow.py auto "creatine" [--max 100] [--want 3] [--big]
  python3 hunt_workflow.py set-jina-key <KEY>

Key: JINA_API_KEY env -> ~/.jina_key (chmod 600). NEVER hardcode.
Pool: pool_gb_fresh.txt (GB, priority) + pool_fresh.txt (general, fallback).
"""
import argparse
import concurrent.futures
import datetime
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import jina_combo as JC
except ImportError as e:
    print(f"FAILED to import jina_combo.py: {e}")
    sys.exit(1)

GB_POOL = os.path.join(HERE, "pool_gb_fresh.txt")
BIG_POOL = os.path.join(HERE, "pool_fresh.txt")
OUT_DIR = os.path.join(HERE, "hunt_out")

POOL_SOURCES = {
    "proxifly_gb_json": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/countries/GB/data.json",
    "monosans_http": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "thespeedx": "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "vakhov_http": "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
    "vakhov_https": "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/https.txt",
}


def _fetch_text(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def cmd_prep(big: bool = False) -> dict:
    """PREP: prepare all env. Fetch fresh pools + merge old stock + dedup.

    big=False: GB only (fast, ~300). big=True: + general sources (thousands).
    Return summary {gb, big, key_ok}.
    """
    cands_gb: set = set()
    cands_all: set = set()
    log = []
    # 1. Proxifly GB (GBP priority)
    try:
        raw = _fetch_text(POOL_SOURCES["proxifly_gb_json"])
        d = json.loads(raw.decode("utf-8", errors="ignore"))
        fresh = [x["proxy"] for x in d if x.get("proxy")]
        cands_gb.update(fresh)
        cands_all.update(fresh)
        log.append(f"proxifly GB: {len(fresh)}")
    except Exception as e:  # noqa: BLE001
        log.append(f"proxifly GB FAIL: {str(e)[:80]}")
    # 2. General sources (if --big)
    if big:
        for name in ("monosans_http", "thespeedx",
                     "vakhov_http", "vakhov_https"):
            try:
                raw = _fetch_text(POOL_SOURCES[name])
                ips = re.findall(r"\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}",
                                 raw.decode("utf-8", errors="ignore"))
                cands_all.update("http://" + i for i in ips)
                log.append(f"{name}: {len(ips)}")
            except Exception as e:  # noqa: BLE001
                log.append(f"{name} FAIL: {str(e)[:80]}")
    # 3. Merge old stock (keep yesterday's survivors)
    for f in (GB_POOL, BIG_POOL):
        try:
            with open(f, errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        cands_all.add(line)
                        # old GB stock included (proxy:// format, assume GB)
            log.append(f"stock {os.path.basename(f)}: merged")
        except (OSError, IOError):
            pass
    # 4. Write (backup first for rollback)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    targets = [(GB_POOL, sorted(cands_gb))]
    if big:
        targets.append((BIG_POOL, sorted(cands_all)))
    for path, data in targets:
        if not path:
            continue
        if os.path.exists(path) and data:
            os.replace(path, path + f".bak_{ts}")
        if data:
            with open(path, "w") as fh:
                fh.write("\n".join(data) + "\n")
    key = JC.jina_key()
    summary = {"gb": len(cands_gb), "big": len(cands_all) if big else None,
               "key_ok": bool(key), "key_info": JC.key_info(key), "log": log}
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    os.makedirs(OUT_DIR, exist_ok=True)
    return summary


def check_jina() -> dict:
    """Check Jina: key present? live? (cheap example.com probe, not Amazon)."""
    k = JC.jina_key()
    if not k:
        return {"part": "jina", "ok": False, "why": "EMPTY KEY",
                "fix": "hunt_workflow.py set-jina-key <KEY>"}
    r = JC.jina_fetch("https://example.com", key=k, timeout=30)
    if r.get("ok") and r.get("http") == 200 and len(r.get("body", "")) > 200:
        return {"part": "jina", "ok": True,
                "why": f"live 200 len={r.get('len')}",
                "detail": f"key {JC.key_info(k)}"}
    err = r.get("error", "?")
    if r.get("http") in (401, 402):
        return {"part": "jina", "ok": False, "why": err,
                "fix": "key rejected/tier limit - refresh key: set-jina-key <NEW KEY>"}
    return {"part": "jina", "ok": False, "why": f"{err} http={r.get('http')}",
            "fix": "retry / check network / refresh key if 401 repeats"}


def check_proxies(pool_file: str = None, sample: int = 50,
                  url: str = "https://www.amazon.co.uk/s?k=creatine") -> dict:
    """Check proxies: batch gate validation. Return sample alive list."""
    pool = pool_file or (GB_POOL if os.path.exists(GB_POOL) else BIG_POOL)
    try:
        with open(pool, errors="ignore") as fh:
            lines = [l.strip() for l in fh if l.strip()][:sample]
    except (OSError, IOError):
        return {"part": "proxy", "ok": False, "why": f"POOL MISSING: {pool}",
                "fix": "hunt_workflow.py prep", "alive": []}
    if not lines:
        return {"part": "proxy", "ok": False, "why": "POOL EMPTY",
                "fix": "hunt_workflow.py prep --big", "alive": []}
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        res = list(ex.map(lambda px: JC.gate_check(px, url), lines))
    alive = [px for px, code, ok in res if ok]
    ok = len(alive) > 0
    return {"part": "proxy", "ok": ok,
            "why": f"sample={len(lines)} alive={len(alive)}",
            "fix": None if ok else "refresh + search: raise sample / prep --big",
            "alive": alive,
            "pool": pool}


def cmd_check(sample: int = 50) -> dict:
    """CHECK: Good/Bad verdict PER COMPONENT (jina vs proxy separately)."""
    j = check_jina()
    p = check_proxies(sample=sample)
    verdict = {"jina": j, "proxy": p,
               "all_good": bool(j.get("ok") and p.get("ok"))}
    print(json.dumps(verdict, indent=1, ensure_ascii=False))
    if verdict["all_good"]:
        print("VERDICT: Good -> proceed to HUNT ")
    else:
        bad = [k for k in ("jina", "proxy") if not verdict[k].get("ok")]
        print(f"VERDICT: Bad: {', '.join(bad)} ")
        for k in bad:
            print(f"  - {k}: {verdict[k].get('why')} | fix: {verdict[k].get('fix')}")
    return verdict


def content_check(proxy: str, key: str,
                  url: str = "https://www.amazon.co.uk/s?k=creatine",
                  timeout: int = 60) -> dict:
    """CONTENT-CHECK: 1 cheap Jina request via proxy -> real GBP verdict.

    Gate 202 is NOT enough (proof: 3 gate-alive proxies = 1KB empty content).
    genuinely-alive = GBP-guard passes (GBP >= JC.GBP_MIN, not US, has ASIN).
    Quota-saving: only for gate-alive candidates, not the whole pool.
    Return {proxy, ok, pound, asins, deliver_us, len, note}.
    """
    r = JC.jina_fetch(url, proxy=proxy, key=key, timeout=timeout)
    ok = bool(r.get("ok") and r.get("gbp_pass")
              and not r.get("deliver_us"))
    return {"proxy": proxy, "ok": ok,
            "http": r.get("http"), "len": r.get("len", 0),
            "asins": r.get("asins", 0), "pound": r.get("pound", 0),
            "deliver_us": r.get("deliver_us"),
            "error": r.get("error", ""),
            "note": ("GENUINE, GBP passes" if ok
                     else "gate-only/zombie (202 but empty content)")}


def refresh_proxies(want: int = 3, batch: int = 50, max_batches: int = 20,
                    pool_file: str = None, key: str = None,
                    content_check_on: bool = True) -> dict:
    """FIX proxy: batch gate-check + CONTENT-CHECK per live candidate.

    Two filters: (1) fast curl gate ($0, no Jina quota),
    (2) Jina content-check (1 req/candidate) - only GBP passers
    become genuinely-alive. Quota-saving: filter-2 only for
    filter-1 survivors.
    """
    pools = [pool_file] if pool_file else [GB_POOL, BIG_POOL]
    found: list = []
    checked = 0
    for pool in pools:
        if len(found) >= want:
            break
        if not pool or not os.path.exists(pool):
            continue
        with open(pool, errors="ignore") as fh:
            lines = [l.strip() for l in fh if l.strip()]
        for i in range(0, len(lines), batch):
            if len(found) >= want or (i // batch) >= max_batches:
                break
            chunk = lines[i:i + batch]
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=20) as ex:
                res = list(ex.map(lambda px: JC.gate_check(px), chunk))
            gate_ok = [px for px, code, ok in res if ok and px not in found]
            checked += len(chunk)
            if not gate_ok:
                print(f"[search] {os.path.basename(pool)} batch {i // batch + 1}: "
                      f"gate 0/{len(chunk)} -> continue", flush=True)
                continue
            # FILTER-2: GBP content-check (1 Jina req/candidate, only gate-ok)
            # PARALLEL max 3 (time /3, same quota; free-key 500 RPM is safe)
            real = []
            if content_check_on and key:
                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=3) as ex:
                    ccs = list(ex.map(lambda px: content_check(px, key),
                                      gate_ok))
                for px, cc in zip(gate_ok, ccs):
                    if len(found) + len(real) >= want:
                        break
                    tag = "GENUINE" if cc["ok"] else "zombie"
                    print(f"  [content] {px}: GBP={cc['pound']} "
                          f"asin={cc['asins']} US={cc['deliver_us']} -> {tag}",
                          flush=True)
                    if cc["ok"]:
                        real.append(px)
            else:
                real = gate_ok  # fallback: no key / disabled
            found.extend(real)
            print(f"[search] {os.path.basename(pool)} batch {i // batch + 1}: "
                  f"gate {len(gate_ok)}/{len(chunk)}, "
                  f"genuine +{len(real)} (total {len(found)}/{want})",
                  flush=True)
    return {"alive": found[:max(want, len(found))], "checked": checked,
            "want": want, "ok": len(found) > 0}


def hunt_one(keyword: str, proxies: list, key: str,
             timeout: int = 60, l3_fallback: bool = True,
             l3_max: int = 3) -> dict:
    """HUNT 1 keyword: Jina combo first, all fail -> L3-rotator FALLBACK.

    L3 = local StealthyFetcher + ranked proxies (pool_ranked.json),
    auto-rotate until SUCCESS/PARTIAL. Return dict + save winner file.
    """
    url = f"https://www.amazon.co.uk/s?k={keyword.replace(' ', '+')}"
    slug = re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_")
    r0 = JC.jina_fetch(url, proxy=None, key=key, timeout=timeout)
    print(f"[probe] '{keyword}' no-proxy: http={r0.get('http')} "
          f"len={r0.get('len', 0)} asin={r0.get('asins', 0)} "
          f"GBP={r0.get('pound', 0)} US={r0.get('deliver_us')}", flush=True)
    cands = [r0]
    winner = r0 if r0.get("gbp_pass") else None
    used_proxy = None
    if not winner:
        for px in proxies:
            r = JC.jina_fetch(url, proxy=px, key=key, timeout=timeout)
            print(f"[combo] '{keyword}' {px}: http={r.get('http')} "
                  f"len={r.get('len', 0)} asin={r.get('asins', 0)} "
                  f"GBP={r.get('pound', 0)} {r.get('error', '') or ''}",
                  flush=True)
            cands.append(r)
            if r.get("gbp_pass"):
                winner, used_proxy = r, px
                break
    out = {"keyword": keyword, "url": url, "ok": bool(winner),
           "via": used_proxy or ("no-proxy" if winner else None),
           "probe": {x: r0.get(x) for x in
                     ("http", "len", "asins", "pound", "usd",
                      "deliver_us", "cached", "error")}}
    if winner:
        stats = {x: winner.get(x) for x in
                 ("http", "len", "asins", "pound", "usd", "ratings",
                  "deliver_us", "cached", "sorry")}
        stats["asin_sample"] = winner.get("asin_sample", [])[:10]
        stats["pound_sample"] = winner.get("pound_sample", [])[:10]
        out["result"] = stats
        md_path = os.path.join(OUT_DIR, f"hunt_{slug}.md")
        with open(md_path, "w") as fh:
            fh.write(winner.get("body", ""))
        rec_path = os.path.join(OUT_DIR, f"hunt_{slug}.json")
        recs = {"keyword": keyword, "url": url, "via": out["via"],
                "asins": sorted(set(re.findall(
                    r"/dp/([A-Z0-9]{10})", winner.get("body", "")))),
                "prices_gbp": re.findall(r"\u00a3\s*[\d.,]+",
                                         winner.get("body", ""))[:60],
                "stats": stats}
        with open(rec_path, "w") as fh:
            json.dump(recs, fh, indent=1, ensure_ascii=False)
        out["files"] = [md_path, rec_path]
        out["asin_count"] = len(recs["asins"])
        print(f"[WIN] '{keyword}' via {out['via']}: "
              f"asin={out['asin_count']} GBP={stats['pound']} -> {rec_path}",
              flush=True)
    else:
        # L3 FALLBACK: Jina empty everywhere -> local StealthyFetcher + rotator.
        # Local import so the workflow still runs without scrapling installed.
        if not l3_fallback:
            out["error"] = "GBP passed on no Jina candidate"
            return out
        print(f"[fallback-L3] '{keyword}': Jina empty -> L3 rotator...",
              flush=True)
        try:
            from uscraper import (fetch_l3 as _l3, detect as _det,
                                  count_asin as _ca, TARGETS as _TG)
            import rotator as _ROT
        except ImportError as e:
            out["error"] = f"Jina empty + L3 unavailable: {e}"
            return out
        pool = _ROT.load_pool(sejati_only=True)[:l3_max]
        if not pool:
            pool = [p for p in proxies][:l3_max]
        if not pool:
            out["error"] = "Jina empty + empty L3 pool"
            return out
        for px in pool:
            code, html, err = _l3(url, px)
            sig = _det(code, html, _TG["amazon-uk"])
            print(f"[L3] '{keyword}' {px}: status={code} "
                  f"len={len(html)} asin={_ca(html)} "
                  f"GBP={html.count(chr(163))} -> {sig}", flush=True)
            if sig in ("SUCCESS", "PARTIAL"):
                asins = sorted(set(re.findall(r"/dp/([A-Z0-9]{10})",
                                              html)))
                prices = re.findall(chr(163) + r"\s*[\d.,]+", html)[:60]
                html_path = os.path.join(OUT_DIR, f"hunt_{slug}.html")
                with open(html_path, "w") as fh:
                    fh.write(html)
                rec_path = os.path.join(OUT_DIR, f"hunt_{slug}.json")
                recs = {"keyword": keyword, "url": url,
                        "via": f"L3-fallback {px}",
                        "asins": asins, "prices_gbp": prices,
                        "stats": {"http": code, "len": len(html),
                                  "asins": len(asins),
                                  "pound": len(prices),
                                  "signal": sig}}
                with open(rec_path, "w") as fh:
                    json.dump(recs, fh, indent=1, ensure_ascii=False)
                out.update({"ok": True, "via": f"L3-fallback {px}",
                            "result": recs["stats"],
                            "files": [html_path, rec_path],
                            "asin_count": len(asins)})
                print(f"[WIN-L3] '{keyword}' via {px}: "
                      f"asin={len(asins)} GBP={len(prices)} -> {rec_path}",
                      flush=True)
                return out
        out["error"] = (f"Jina empty ({len(proxies)} proxy) + "
                        f"L3 empty ({len(pool)} proxy)")
    return out


def cmd_hunt(keywords: list, max_n: int = 50, want: int = 3,
             timeout: int = 60, no_content_check: bool = False,
             no_l3: bool = False, l3_max: int = 3) -> dict:
    """HUNT: check -> fix (gate + content-check) -> Jina; on fail -> L3 fallback."""
    os.makedirs(OUT_DIR, exist_ok=True)
    verdict = cmd_check(sample=min(max_n, 50))
    key = JC.jina_key()
    proxies: list = verdict["proxy"].get("alive", [])
    if not verdict["jina"].get("ok"):
        return {"ok": False, "error": "JINA Bad",
                "fix": verdict["jina"].get("fix"), "verdict": verdict}
    if not proxies or len(proxies) < 1:
        print("[fix] proxy Bad/short -> refresh + search + content-check...",
              flush=True)
        fr = refresh_proxies(want=want, batch=50, max_batches=20,
                             key=key,
                             content_check_on=not no_content_check)
        proxies = fr["alive"]
        print(f"[fix] got {len(proxies)}/{want} (checked {fr['checked']})",
              flush=True)
        if not proxies:
            # tetap hunt via probe no-proxy (discovery ASIN $0)
            print("[fix] zero proxies - hunt in discovery mode (no proxy)",
                  flush=True)
    results = [hunt_one(kw, proxies, key, timeout,
                        l3_fallback=not no_l3, l3_max=l3_max)
               for kw in keywords]
    summary = {"ok": any(r["ok"] for r in results),
               "keywords": len(results),
               "won": sum(1 for r in results if r["ok"]),
               "results": results}
    spath = os.path.join(
        OUT_DIR, "hunt_summary.json")
    with open(spath, "w") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False,
                  default=str)
    print(f"SUMMARY: {summary['won']}/{summary['keywords']} won "
          f"-> {spath}")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hunt_workflow",
                                 description="prep -> check -> fix -> hunt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prep", help="prepare all env (fresh pools + key check)")
    pp.add_argument("--big", action="store_true",
                    help="also fetch general pools, thousands (slow)")
    ck = sub.add_parser("check", help="check all env -> Good/Bad verdict per part")
    ck.add_argument("--sample", type=int, default=50)
    hh = sub.add_parser("hunt", help="hunt keywords (auto check+fix first)")
    hh.add_argument("keywords", nargs="+")
    hh.add_argument("--max", type=int, default=50)
    hh.add_argument("--want", type=int, default=3)
    hh.add_argument("--timeout", type=int, default=60)
    hh.add_argument("--no-content-check", action="store_true",
                    help="skip content-check (fast, but alive verdict = gate only)")
    hh.add_argument("--no-l3", action="store_true",
                    help="disable L3 fallback (Jina only)")
    hh.add_argument("--l3-max", type=int, default=3,
                    help="max L3 fallback proxies per keyword")
    at = sub.add_parser("auto", help="prep -> check -> fix -> hunt in one go")
    at.add_argument("keywords", nargs="+")
    at.add_argument("--max", type=int, default=100)
    at.add_argument("--want", type=int, default=3)
    at.add_argument("--big", action="store_true")
    at.add_argument("--timeout", type=int, default=60)
    at.add_argument("--no-content-check", action="store_true")
    at.add_argument("--no-l3", action="store_true")
    at.add_argument("--l3-max", type=int, default=3)
    sk = sub.add_parser("set-jina-key", help="store a new Jina key (refresh)")
    sk.add_argument("key")
    a = ap.parse_args(argv)

    if a.cmd == "prep":
        cmd_prep(big=a.big)
        return 0
    if a.cmd == "check":
        v = cmd_check(sample=a.sample)
        return 0 if v["all_good"] else 2
    if a.cmd == "hunt":
        s = cmd_hunt(a.keywords, max_n=a.max, want=a.want,
                     timeout=a.timeout,
                     no_content_check=a.no_content_check,
                     no_l3=a.no_l3, l3_max=a.l3_max)
        return 0 if s["ok"] else 3
    if a.cmd == "auto":
        print("=== PREP ===", flush=True)
        cmd_prep(big=a.big)
        print("=== HUNT ===", flush=True)
        s = cmd_hunt(a.keywords, max_n=a.max, want=a.want,
                     timeout=a.timeout,
                     no_content_check=a.no_content_check,
                     no_l3=a.no_l3, l3_max=a.l3_max)
        return 0 if s["ok"] else 3
    if a.cmd == "set-jina-key":
        return JC.cmd_set_key(a.key) if hasattr(JC, "cmd_set_key") else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
