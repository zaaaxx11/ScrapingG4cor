"""Site registry: all supported targets, one dict per site.

Each entry describes HOW to fetch it and WHAT counts as success,
so the fetch chain (fetch_chain.py) stays generic: new site = new
dict here, no workflow rewrite.

Success = content signals (price markers, catalog JSON, product
tiles), never HTTP status alone.
"""

SITES = {
    "amazon-uk": {
        "label": "Amazon UK",
        "kind": "waf",
        "search_url": "https://www.amazon.co.uk/s?k={q}",
        "method": "Jina render + X-Proxy-Url (GB proxy) + key; L3 StealthyFetcher fallback",
        "price_markers": ["\u00a3", "a-offscreen", "a-price"],
        "ok_markers": ["data-asin"],
        "min_len": 20000,
        "notes": "AWS WAF. Anonymous Jina gives ASIN discovery (US locale, 0 GBP); "
                 "GBP prices need key + GB proxy, or L3 fallback.",
    },
    "active-sports-nutrition": {
        "label": "Active Sports Nutrition",
        "kind": "search-page",
        "search_url": "https://www.activesportsnutrition.co.uk/Search?q={q}&size=100&skip=0",
        "method": "Direct GET paginated (skip=0..300). Split tiles on "
                 "`product-list-item-title`, name from `productDetailLink`, "
                 "price from `finalPrice.amountIncVat` JSON",
        "price_markers": ["amountIncVat", "£"],
        "ok_markers": ["product-list-item-title", "activesportsnutrition",
                       "results for", "Search"],
        "min_len": 3000,
        "notes": "Anonymous Jina works.",
    },
    "dolphin-fitness": {
        "label": "Dolphin Fitness",
        "kind": "search-page",
        "search_url": "https://www.dolphinfitness.co.uk/en/creatine/list/1",
        "method": "Direct GET list pages 1..9. Split on `ari-pc pg-pc` cells, "
                 "name from link `title`, price = lowest `GBP` in cell",
        "price_markers": ["£"],
        "ok_markers": ["dolphinfitness", "results for", "In Stock",
                       "pg-pc"],
        "min_len": 3000,
        "notes": "Anonymous Jina works.",
    },
    "holland-and-barrett": {
        "label": "Holland & Barrett",
        "kind": "challenge",
        "search_url": "https://www.hollandandbarrett.com/shop/sports-nutrition/creatine/",
        "method": "Jina Reader (direct returns a 202 challenge page). "
                 "Parse markdown product links, name + GBP from link label",
        "price_markers": ["\u00a3"],
        "ok_markers": ["hollandandbarrett.com/shop/product"],
        "min_len": 20000,
        "notes": "Anonymous Jina works (proven 165 KB). Retry up to 4x, 8s apart.",
    },
    "applied-nutrition": {
        "label": "Applied Nutrition",
        "kind": "shopify",
        "search_url": "https://appliednutrition.uk/products.json?limit=250&page=1",
        "method": "Shopify `/products.json` paginated (250/page). "
                 "Title + variant title, variant price + barcode",
        "price_markers": ['"price"', '"products"'],
        "ok_markers": ['"products"'],
        "min_len": 5000,
        "notes": "Anonymous Jina returns raw JSON. Direct GET also works.",
    },
    "10x-athletic": {
        "label": "10X Athletic",
        "kind": "shopify",
        "search_url": "https://www.10xathletic.com/products.json?limit=250&page=1",
        "method": "Shopify `/products.json` paginated (250/page). "
                 "Title + variant title, variant price + barcode",
        "price_markers": ['"price"', '"products"'],
        "ok_markers": ['"products"'],
        "min_len": 2000,
        "notes": "Direct GET works.",
    },
    "animal-pak": {
        "label": "Animal Pak",
        "kind": "shopify",
        "search_url": "https://uk.animalpak.com/products.json?limit=250&page=1",
        "method": "Shopify `/products.json` paginated (250/page). "
                 "Title + variant title, variant price + barcode",
        "price_markers": ['"price"', '"products"'],
        "ok_markers": ['"products"'],
        "min_len": 2000,
        "notes": "Direct GET works.",
    },
    "cellucor-uk": {
        "label": "Cellucor UK",
        "kind": "product-pages",
        "search_url": "https://www.cellucor.uk/",
        "method": "Homepage `/product/*` links, visit each page. Price from "
                 "JSON-LD (`price` + `priceCurrency: GBP`, or `lowPrice`, or GBP), "
                 "name from `<title>`, barcode from `gtin13`",
        "price_markers": ["priceCurrency", "£"],
        "ok_markers": ["/product/", "cellucor", "Add to Cart",
                       "Add to basket"],
        "min_len": 3000,
        "notes": "Direct GET works.",
    },
    "iherb-uk": {
        "label": "iHerb UK",
        "kind": "search-page",
        "search_url": "https://uk.iherb.com/search?kw={q}&p=1",
        "method": "Search pages `search?kw=..&p=1..20`. Split on `data-product-id`, "
                 "price from `discountPrice` / `data-ga-discount-price`, "
                 "name from `itemprop=\"name\"` (Jina fallback if blocked)",
        "price_markers": ["discountPrice", "£"],
        "ok_markers": ["data-product-id", "iherb.com/pr/", "Add to Cart",
                       "results for"],
        "min_len": 3000,
        "notes": "Direct first, Jina fallback.",
    },
    "reflex-nutrition": {
        "label": "Reflex Nutrition",
        "kind": "shopify",
        "search_url": "https://reflexnutrition.com/products.json?limit=250&page=1",
        "method": "Shopify `/products.json` paginated (250/page). "
                 "Title + variant title, variant price + barcode",
        "price_markers": ['"price"', '"products"'],
        "ok_markers": ['"products"'],
        "min_len": 2000,
        "notes": "Direct GET works.",
    },
}

DEFAULT_SITE = "amazon-uk"

USER_FILE = "targets.json"


def _user_path() -> str:
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, USER_FILE)


def user_sites() -> dict:
    """Sites the USER defined (targets.json, git-ignored)."""
    import json
    import os
    try:
        with open(_user_path(), errors="ignore") as fh:
            d = json.load(fh)
        sites = d.get("sites", {}) if isinstance(d, dict) else {}
        return sites if isinstance(sites, dict) else {}
    except (OSError, ValueError):
        return {}


def merged() -> dict:
    """Builtin defaults + user targets (user wins on key clash)."""
    m = dict(SITES)
    m.update(user_sites())
    return m


def add_user_site(key: str, entry: dict) -> str:
    """Save a user-defined target into targets.json. Return the path."""
    import json
    import os
    k = (key or "").strip().lower().replace(" ", "-")
    if not k or not isinstance(entry, dict) or not entry.get("search_url"):
        raise ValueError("need a key + entry with at least search_url")
    path = _user_path()
    data = {"sites": user_sites()}
    data["sites"][k] = entry
    with open(path, "w") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.chmod(path, 0o600)
    return path


def remove_user_site(key: str) -> bool:
    """Delete a user-defined target. True if it existed."""
    import json
    k = (key or "").strip().lower()
    data = {"sites": user_sites()}
    if k not in data["sites"]:
        return False
    del data["sites"][k]
    with open(_user_path(), "w") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    return True


def get(name: str) -> dict:
    """Return the site dict (user targets win), fallback to amazon-uk."""
    m = merged()
    return m.get((name or "").lower(), m[DEFAULT_SITE])


def names() -> list:
    """All supported site keys: builtin + user-defined (stable order)."""
    return list(merged())
