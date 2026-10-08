"""ScrapingG4cor - one door for free scraping of any site.

Thin wrapper: every command below is one import + one call into the
small modules next to this file. No logic lives here.

Usage:
  python3 scrape.py fetch URL [--site KEY] [--pool pool.txt] [--out page.md]
  python3 scrape.py sites                       # builtin + your targets
  python3 scrape.py my-sites                    # your targets only
  python3 scrape.py add-site KEY URL [--kind K] [--price P] [--ok O]
  python3 scrape.py remove-site KEY
  python3 scrape.py hunt "kw1" "kw2" ...        # batch via hunt_workflow
  python3 scrape.py prep [--big]                # refresh free-proxy pools
  python3 scrape.py key guide|set|status        # free Jina key helper

Chain per fetch (cheapest working layer wins, content-verified):
  L0 anonymous Jina ($0) -> L1 keyed Jina -> L2 key + free proxy -> L3 local.
"""
import sys

COMMANDS = ("fetch", "sites", "my-sites", "add-site", "remove-site",
            "hunt", "prep", "key")


def usage() -> int:
    print((__doc__ or "scrape.py").strip())
    return 2


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        return usage()
    cmd, rest = args[0], args[1:]

    if cmd in ("fetch", "sites", "my-sites", "add-site", "remove-site"):
        import fetch_chain as FC
        names = {"fetch": "fetch", "sites": "sites",
                 "my-sites": "my-sites", "add-site": "add-site",
                 "remove-site": "remove-site"}
        return FC.main([names[cmd], *rest])

    if cmd in ("hunt", "prep"):
        import hunt_workflow as HW
        # hunt_workflow uses: auto|prep|check|hunt ...  -> map simply:
        if cmd == "prep":
            return HW.main(["prep", *rest])
        return HW.main(["hunt", *rest])

    if cmd == "key":
        import get_key as GK
        return GK.main(rest or ["status"])
    return usage()


if __name__ == "__main__":
    sys.exit(main())
