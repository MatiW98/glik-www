# glik-www

Public website of the Glik app (iOS and Android), served by GitHub Pages at
<https://matiw98.github.io/glik-www/>. The app's source code is private; this
repository holds only what the website and the app download.

| File | What it is |
|---|---|
| `index.html` | Support page |
| `prywatnosc.html` | Privacy policy (source of truth: `docs/` in the app repository) |
| `products.json` | Built-in product database update, checked by the app at startup. Edited by hand from the app repository, never by a script here |
| `offline-pack.json` | Offline database: about 1,000 popular Open Food Facts products in one file |
| `tools/build_offline_pack.py` | Builds `offline-pack.json` |
| `.github/workflows/offline-pack.yml` | Rebuilds `offline-pack.json` every week |

## offline-pack.json

When a user taps "Pobierz" (Download) in the app's Settings, the app downloads
this one file instead of paging through the Open Food Facts search API itself
(10 pages of 100 products, at most 10 requests a minute, so about 2 minutes).
If the file is unavailable or invalid, the app falls back to that paging.

Shape (format 1, compact JSON):

```json
{"format":1,"generated":"2026-10-02T12:47:30Z","source":"Open Food Facts",
 "license":"Open Database License (ODbL) 1.0; ...","count":1000,
 "products":[{"code":"...","product_name":"...","nutriments":{...}, ...}]}
```

`products` holds the fields the app requests from the Open Food Facts search
(`code`, names, brands, `nutriments`, serving and package size, Nutri-Score,
NOVA, `countries_tags`), with `nutriments` trimmed to the eight per-100 g values
the app reads. Products without a barcode, and repeats of a barcode, are
dropped. The values themselves are not changed.

### Refresh

- Automatically: the "Offline pack" workflow runs every Monday at 04:17 UTC and
  commits `offline-pack.json` only when the products changed.
- By hand: `python3 tools/build_offline_pack.py` (Python 3, standard library
  only, about 2 minutes), then commit and push the file. You can also start the
  workflow from the Actions tab or with
  `gh workflow run offline-pack.yml -R MatiW98/glik-www`.

A run that fails on any page, or ends with fewer than 300 products, exits with
an error and leaves the existing file untouched.

## Licence and attribution

The product data comes from [Open Food Facts](https://world.openfoodfacts.org)
and is © Open Food Facts contributors.

`offline-pack.json` is a derivative database of the Open Food Facts database.
It is made available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Individual contents of the database are under the
[Database Contents License (DbCL) 1.0](https://opendatacommons.org/licenses/dbcl/1-0/).
If you publicly use this database or a database derived from it, you must
attribute Open Food Facts and keep it under the ODbL (share-alike), as the
licence requires. The script that produced it is `tools/build_offline_pack.py`
in this repository.
