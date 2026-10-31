#!/usr/bin/env python3
"""Builds offline-pack.json: the Glik app's offline database in one file.

The app used to page through Open Food Facts' anonymous search API itself
(10 pages of 100, at most 10 requests a minute, frequent 503s), which took
about 2 minutes. It now downloads this one pre-built file in seconds and only
falls back to paging OFF when the file is unavailable or invalid.

Same query as the app (app/lib/data/off_pack_service.dart): the same fields,
no sort, page_size=100, pages 1..10. Standard library only.

    python3 tools/build_offline_pack.py            # writes ./offline-pack.json
    python3 tools/build_offline_pack.py --out x.json

Exit code 0: the file was written, or the products are unchanged. OFF often
answers 503 for minutes at a time, so a page that keeps failing after page 1
(or the run's time budget running out) ends the fetch early: the pages that
did arrive are kept and the missing ones are filled from the existing pack,
so coverage never shrinks. Exit code 1 only when page 1 itself fails or fewer
than MIN_PRODUCTS products remain; the existing file is then left untouched.
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
import urllib.request
from datetime import datetime, timezone

SEARCH = "https://world.openfoodfacts.org/api/v2/search"
# Copied from the app's OffPackService._fetchPage; keep the two in sync.
FIELDS = ("code,product_name,product_name_en,brands,nutriments,"
          "serving_size,serving_quantity,nutriscore_grade,nova_group,quantity,"
          "countries_tags")
PAGE_SIZE = 100
LAST_PAGE = 10  # OFF answers 401 to anonymous clients beyond page 10
# The keys the app reads from `nutriments` (lib/logic/off_mapper.dart and
# off_quality.dart). Everything else OFF sends there is dropped.
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
USER_AGENT = "GlikPackBuilder/1.0 (mateuszwyszogrodzki@o2.pl)"
PAGE_DELAY = 6.5  # OFF allows 10 search requests a minute per IP
MAX_ATTEMPTS = 10
MAX_BACKOFF = 60.0  # OFF's 503 bursts often last minutes, not seconds
RUN_BUDGET = 22 * 60  # seconds; the workflow job times out at 30 minutes
TIMEOUT = 30
MIN_PRODUCTS = 300
LICENSE = ("Open Database License (ODbL) 1.0; product data © Open Food Facts "
           "contributors, https://world.openfoodfacts.org")


class EndOfData(Exception):
    """OFF refused a page after the first: the anonymous limit, not an error."""


def backoff(attempt: int, retry_after: str | None = None) -> float:
    """2, 4, 8 ... capped at MAX_BACKOFF; OFF's Retry-After wins when sane."""
    if retry_after and retry_after.strip().isdigit():
        return min(max(float(retry_after), 1.0), 120.0)
    return min(2.0 * (2 ** attempt), MAX_BACKOFF)


class OutOfTime(Exception):
    """The run's time budget ran out between retries."""


def fetch_page(page: int, deadline: float = float("inf")) -> list:
    url = (f"{SEARCH}?fields={FIELDS}&page_size={PAGE_SIZE}&page={page}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = "no attempt"
    for attempt in range(MAX_ATTEMPTS):
        retry_after = None
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and page > 1:
                raise EndOfData() from e
            if e.code < 500 and e.code != 429:
                raise RuntimeError(f"page {page}: HTTP {e.code}") from e
            last = f"HTTP {e.code}"
            retry_after = e.headers.get("Retry-After") if e.headers else None
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError,
                OSError) as e:
            last = f"{type(e).__name__}: {e}"
        else:
            try:
                data = json.loads(body)
            except ValueError:
                data = None  # truncated or HTML body: retry like a 5xx
            products = data.get("products") if isinstance(data, dict) else None
            if isinstance(products, list):
                return products
            last = "malformed body"
        if attempt + 1 < MAX_ATTEMPTS:
            wait = backoff(attempt, retry_after)
            if time.monotonic() + wait > deadline:
                raise OutOfTime(f"page {page}: time budget used up ({last})")
            print(f"  page {page}: {last}, retry in {wait:g} s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"page {page}: gave up after {MAX_ATTEMPTS} attempts ({last})")


def trim(product: dict) -> dict | None:
    code = product.get("code")
    if not isinstance(code, str) or not code.strip():
        return None
    # Only the fields the query asked for. OFF adds unrequested ones next to
    # `nutriments` (nutriments_estimated alone is ~45 % of the file); the app
    # reads none of them. Requested fields stay as OFF returned them.
    out = {k: product[k] for k in FIELDS.split(",") if k in product}
    out["code"] = code.strip()
    nutr = product.get("nutriments")
    if isinstance(nutr, dict):
        out["nutriments"] = {k: nutr[k] for k in NUTRIMENT_KEYS if k in nutr}
    return out


def collect() -> tuple[list, bool]:
    """Products in OFF order, and whether every page arrived."""
    products, seen = [], set()
    deadline = time.monotonic() + RUN_BUDGET
    complete = True
    for page in range(1, LAST_PAGE + 1):
        if page > 1:
            time.sleep(PAGE_DELAY)
        try:
            raw = fetch_page(page, deadline)
        except EndOfData:
            print(f"page {page}: refused (anonymous limit), end of data")
            break
        except (RuntimeError, OutOfTime) as e:
            if page == 1:
                raise
            print(f"page {page}: {e}; keeping pages 1-{page - 1}", file=sys.stderr)
            complete = False
            break
        kept = 0
        for item in raw:
            p = trim(item) if isinstance(item, dict) else None
            if p is None or p["code"] in seen:
                continue
            seen.add(p["code"])
            products.append(p)
            kept += 1
        print(f"page {page}: {len(raw)} received, {kept} kept")
        if len(raw) < PAGE_SIZE:
            break  # last page
    return products, complete


def existing_products(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("products")
    except (OSError, ValueError, AttributeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_out = pathlib.Path(__file__).resolve().parent.parent / "offline-pack.json"
    ap.add_argument("--out", type=pathlib.Path, default=default_out)
    out = ap.parse_args().out

    try:
        products, complete = collect()
    except Exception as e:  # noqa: BLE001 - any failure must keep the old file
        print(f"FAILED: {e}; {out.name} left unchanged", file=sys.stderr)
        return 1
    if not complete:
        # Fill the pages OFF did not serve from the current pack, so a partial
        # run refreshes what it got without dropping anything.
        seen = {p["code"] for p in products}
        old = existing_products(out) or []
        filled = [p for p in old if isinstance(p, dict) and p.get("code") not in seen]
        products += filled
        print(f"partial fetch: {len(filled)} products carried over from the existing pack")
    if len(products) < MIN_PRODUCTS:
        print(f"FAILED: only {len(products)} products (< {MIN_PRODUCTS}); "
              f"{out.name} left unchanged", file=sys.stderr)
        return 1
    if existing_products(out) == products:
        print(f"{len(products)} products, unchanged; {out.name} not rewritten")
        return 0

    pack = {
        "format": 1,
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
    print(f"wrote {out} ({len(products)} products, {len(text.encode('utf-8'))} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
