#!/usr/bin/env python3
"""Builds the Glik app's offline packs: one file per market.

    offline-pack.json      Poland: products with a Polish label sold in Poland
    offline-pack-en.json   English: products with an English label sold in the
                           United Kingdom, Ireland, the United States, Canada,
                           Australia or New Zealand

Each pack holds the PACK_SIZE most scanned such products of Open Food Facts.
Until November 2026 there was one pack from OFF's unsorted world search: 78 %
of it was sold in France and 3.5 % in Poland, so a Polish user got a thousand
products they could not buy.

`offline-pack.json` keeps its name because Glik 1.3.0-1.4.0 downloads exactly
that URL; most of their users are in Poland. Glik 1.5 and later picks the
file by the app language and the phone's region.

Source: Open Food Facts' search service (search.openfoodfacts.org), which
filters by country and label language and sorts by scans in one request.
Standard library only.

    python3 tools/build_offline_pack.py              # both packs
    python3 tools/build_offline_pack.py --market pl  # one pack
    python3 tools/build_offline_pack.py --out-dir /tmp/packs

Exit code 0: every requested pack was written or is unchanged. A pack whose
fetch fails, or that ends with fewer than MIN_PRODUCTS products, is left
untouched and the exit code is 1; the other pack is still written.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import pathlib
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SEARCH = "https://search.openfoodfacts.org/search"
PACKS = {
    "pl": {
        "file": "offline-pack.json",
        "query": 'countries_tags:"en:poland" AND lang:pl',
    },
    "en": {
        "file": "offline-pack-en.json",
        "query": ('lang:en AND countries_tags:("en:united-kingdom" OR '
                  '"en:ireland" OR "en:united-states" OR "en:canada" OR '
                  '"en:australia" OR "en:new-zealand")'),
    },
}
# What the app reads from a pack row (app/lib/logic/off_mapper.dart). The
# search service does not index serving sizes, so pack rows carry none; the
# app then offers 100 g and the package.
FIELDS = ("code,product_name,product_name_en,brands,nutriments,"
          "nutriscore_grade,nova_group,quantity,countries_tags")
# The keys the app reads from `nutriments`. Everything else is dropped.
NUTRIMENT_KEYS = (
    "carbohydrates_100g",
    "fiber_100g",
    "sugars_100g",
    "proteins_100g",
    "fat_100g",
    "saturated-fat_100g",
    "salt_100g",
    "energy-kcal_100g",
)
PACK_SIZE = 1000
PAGE_SIZE = 1000  # the service's maximum
MAX_PAGES = 3  # rows without a name or carbohydrates are skipped, so read on
USER_AGENT = "GlikPackBuilder/2.0 (contact@getglik.com)"
MAX_ATTEMPTS = 6
MAX_BACKOFF = 60.0
TIMEOUT = 60
MIN_PRODUCTS = 300
LICENSE = ("Open Database License (ODbL) 1.0; product data © Open Food Facts "
           "contributors, https://world.openfoodfacts.org")


def backoff(attempt: int) -> float:
    """2, 4, 8 ... seconds, capped at MAX_BACKOFF."""
    return min(2.0 * (2 ** attempt), MAX_BACKOFF)


def fetch_page(query: str, page: int) -> list:
    params = urllib.parse.urlencode({
        "q": query,
        "sort_by": "-unique_scans_n",
        "page_size": PAGE_SIZE,
        "page": page,
        "fields": FIELDS,
    })
    req = urllib.request.Request(f"{SEARCH}?{params}",
                                 headers={"User-Agent": USER_AGENT})
    last = "no attempt"
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise RuntimeError(f"page {page}: HTTP {e.code}") from e
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError,
                OSError) as e:
            last = f"{type(e).__name__}: {e}"
        else:
            try:
                data = json.loads(body)
            except ValueError:
                data = None  # truncated or HTML body: retry like a 5xx
            hits = data.get("hits") if isinstance(data, dict) else None
            if isinstance(hits, list):
                return hits
            last = "malformed body"
        if attempt + 1 < MAX_ATTEMPTS:
            wait = backoff(attempt)
            print(f"  page {page}: {last}, retry in {wait:g} s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"page {page}: gave up after {MAX_ATTEMPTS} attempts ({last})")


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def trim(hit: dict) -> dict | None:
    """One pack row in the shape of OFF's product API, or None to skip it.

    Skipped: no barcode, no name (the app would show the brand alone), no
    carbohydrate value (the app could not compute a glycemic load). The values
    themselves are never changed.
    """
    code = hit.get("code")
    if not isinstance(code, str) or not code.strip():
        return None
    name = hit.get("product_name")
    if not isinstance(name, str) or not name.strip():
        return None
    nutr = hit.get("nutriments")
    if not isinstance(nutr, dict) or not is_number(nutr.get("carbohydrates_100g")):
        return None
    out = {"code": code.strip(), "product_name": name.strip()}
    name_en = hit.get("product_name_en")
    if isinstance(name_en, str) and name_en.strip():
        out["product_name_en"] = name_en.strip()
    # The search service returns brands as a list; OFF's product API, and so
    # the app, has one comma-separated string.
    brands = hit.get("brands")
    if isinstance(brands, list):
        brands = ", ".join(b.strip() for b in brands if isinstance(b, str) and b.strip())
    if isinstance(brands, str) and brands.strip():
        out["brands"] = brands.strip()
    out["nutriments"] = {k: nutr[k] for k in NUTRIMENT_KEYS if is_number(nutr.get(k))}
    for key in ("nutriscore_grade", "nova_group", "quantity", "countries_tags"):
        if hit.get(key) not in (None, "", []):
            out[key] = hit[key]
    return out


def collect(query: str) -> list:
    """The PACK_SIZE most scanned usable products for [query], in that order."""
    products, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        hits = fetch_page(query, page)
        kept = 0
        for hit in hits:
            p = trim(hit) if isinstance(hit, dict) else None
            if p is None or p["code"] in seen:
                continue
            seen.add(p["code"])
            products.append(p)
            kept += 1
            if len(products) == PACK_SIZE:
                break
        print(f"  page {page}: {len(hits)} received, {kept} kept")
        if len(products) == PACK_SIZE or len(hits) < PAGE_SIZE:
            break
    return products


def existing_products(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("products")
    except (OSError, ValueError, AttributeError):
        return None


def build(market: str, out: pathlib.Path) -> bool:
    """Writes one pack. False when it failed and the file was left alone."""
    print(f"{market}: {out.name}")
    try:
        products = collect(PACKS[market]["query"])
    except Exception as e:  # noqa: BLE001 - any failure must keep the old file
        print(f"FAILED: {e}; {out.name} left unchanged", file=sys.stderr)
        return False
    if len(products) < MIN_PRODUCTS:
        print(f"FAILED: only {len(products)} products (< {MIN_PRODUCTS}); "
              f"{out.name} left unchanged", file=sys.stderr)
        return False
    if existing_products(out) == products:
        print(f"  {len(products)} products, unchanged; {out.name} not rewritten")
        return True

    pack = {
        "format": 1,
        "market": market,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Open Food Facts",
        "license": LICENSE,
        "count": len(products),
        "products": products,
    }
    text = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    # Write next to the target and rename, so a crash never leaves half a file.
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".offline-pack-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)
    print(f"  wrote {out} ({len(products)} products, {len(text.encode('utf-8'))} bytes)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_dir = pathlib.Path(__file__).resolve().parent.parent
    ap.add_argument("--out-dir", type=pathlib.Path, default=default_dir)
    ap.add_argument("--market", choices=sorted(PACKS), action="append",
                    help="build only this pack (repeatable); default: all")
    args = ap.parse_args()
    ok = True
    for market in args.market or sorted(PACKS, reverse=True):  # pl first
        ok = build(market, args.out_dir / PACKS[market]["file"]) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
