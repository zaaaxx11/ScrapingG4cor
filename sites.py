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


def get(name: str) -> dict:
    """Return the site dict, falling back to amazon-uk on unknown names."""
    return SITES.get((name or "").lower(), SITES[DEFAULT_SITE])


def names() -> list:
    """All supported site keys (stable order)."""
    return list(SITES)
