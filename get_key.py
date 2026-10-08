"""Get a Jina Reader API key: status -> guide -> set (validate + store).

The key itself is free (Jina free tier). This script does not mint keys;
it shows where to grab one, validates what you paste (cheap probe +
format check), and stores it in ~/.jina_key (chmod 600).
Never hardcoded, never committed.

Usage:
  python3 get_key.py status
  python3 get_key.py guide
  python3 get_key.py set [KEY]    # omit KEY for hidden prompt
"""
import argparse
import getpass
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import jina_combo as JC
except ImportError as e:
    print(f"FAILED to import jina_combo.py: {e}")
    sys.exit(1)


def cmd_guide() -> int:
    print("""How to get a FREE Jina Reader API key:

  1. Open https://jina.ai/reader in your browser.
  2. Sign in (email / GitHub / Google) and open your dashboard.
  3. Create a Reader API key - it starts with "jina_" (free tier included).
  4. Come back and store it (paste works even with spaces):

       python3 get_key.py set

  Why a key at all? Anonymous Jina works for many pages, but it is
  rate-limited and REJECTS proxy forwarding (401). The free key raises
  the limit and unlocks the L2 proxy combo. The key stays on YOUR
  machine (~/.jina_key, chmod 600) - it is never sent anywhere except
  Jina's own API, and never committed (see .gitignore: *.key).""")
    return 0


def _valid_format(k: str) -> bool:
    return bool(k) and k.startswith("jina_") and len(k) >= 20


def _probe(k: str, timeout: int = 30) -> tuple:
    """Cheap validation: 1 Jina request to example.com. Return (ok, note)."""
    r = JC.jina_fetch("https://example.com", key=k, timeout=timeout)
    if r.get("http") in (401, 402):
        return False, f"key rejected (http={r.get('http')})"
    if r.get("ok") and r.get("http") == 200 and len(r.get("body", "")) > 200:
        return True, f"live (200, len={r.get('len')})"
    return False, f"probe failed ({r.get('error', '?')} http={r.get('http')})"


def cmd_set(raw: str | None = None) -> int:
    k = (raw or "").replace(" ", "").strip()
    if not k:
        k = getpass.getpass("Paste Jina key (hidden): ").replace(
            " ", "").strip()
    if not _valid_format(k):
        print("KEY REJECTED: odd format (must start with jina_, sane length)")
        return 2
    ok, note = _probe(k)
    print(f"validation: {note}")
    if not ok:
        print("NOT stored (fix the key and retry).")
        return 3
    rc = JC.cmd_set_key(k)
    if rc == 0:
        print("Stored + validated. Chain L1/L2 unlocked.")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="get_key",
                                 description="Jina API key helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show key state (no value leaked)")
    sub.add_parser("guide", help="where to grab a free key")
    s = sub.add_parser("set", help="validate + store a key")
    s.add_argument("key", nargs="?", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(f"key: {JC.key_info(JC.jina_key())}")
        return 0 if JC.jina_key() else 2
    if a.cmd == "guide":
        return cmd_guide()
    if a.cmd == "set":
        return cmd_set(a.key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
