# glik-www

Public website of the Glik app (iOS and Android), served by GitHub Pages at
<https://matiw98.github.io/glik-www/>. The app's source code is private; this
repository holds only what the website and the app download.

| File | What it is |
|---|---|
| `index.html` | Support page |
| `prywatnosc.html` | Privacy policy (source of truth: `docs/` in the app repository) |
| `products.json` | Built-in product database update, checked by the app at startup. Edited by hand from the app repository, never by a script here |
| `offline-pack.json` | Offline pack for Poland: the 1,000 most scanned Open Food Facts products with a Polish label sold in Poland |
| `offline-pack-en.json` | Offline pack for the English version: the same for English labels in the United Kingdom, Ireland, the United States, Canada, Australia and New Zealand |
| `tools/build_offline_pack.py` | Builds both packs |
| `.github/workflows/offline-pack.yml` | Rebuilds both packs every week |

## Offline packs

When a user taps "Pobierz" (Download) in the app's Settings, the app downloads
one of these files, so the scanner knows popular packaged products without a
network. Glik 1.5 and later picks the file by market: the Polish pack when the
app speaks Polish or the phone's region is Poland, the English pack otherwise.
Glik 1.3.0-1.4.0 always download `offline-pack.json`, which is why the Polish
pack keeps that name. If the file is unavailable or invalid, the app falls
back to paging Open Food Facts' world search itself.

Until November 2026 there was one pack built from that unsorted world search:
78 % of its products were sold in France and 3.5 % in Poland.

Shape (format 1, compact JSON):

```json
{"format":1,"market":"pl","generated":"2026-11-17T12:47:30Z",
 "source":"Open Food Facts","license":"Open Database License (ODbL) 1.0; ...",
 "count":1000,
 "products":[{"code":"...","product_name":"...","nutriments":{...}, ...}]}
```

`products` are in the shape of Open Food Facts' product API (`code`,
`product_name`, `product_name_en`, `brands`, `nutriments`, `nutriscore_grade`,
`nova_group`, `quantity`, `countries_tags`), most scanned first, with
`nutriments` trimmed to the eight per-100 g values the app reads. Products
without a barcode, a name or a carbohydrate value, and repeats of a barcode,
are dropped. The values themselves are not changed. The source is Open Food
Facts' search service (`search.openfoodfacts.org`), which does not return
serving sizes, so a pack row has none.

### Refresh

- Automatically: the "Offline pack" workflow runs every Monday at 04:17 UTC and
  commits the packs only when their products changed.
- By hand: `python3 tools/build_offline_pack.py` (Python 3, standard library
  only, a few seconds; `--market pl` or `--market en` builds one pack), then
  commit and push the files. You can also start the workflow from the Actions
  tab or with `gh workflow run offline-pack.yml -R MatiW98/glik-www`.

A pack whose download fails, or that ends with fewer than 300 products, is
left untouched and the run exits with an error; the other pack is still
written.

## Licence and attribution

The product data comes from [Open Food Facts](https://world.openfoodfacts.org)
and is © Open Food Facts contributors.

`offline-pack.json` and `offline-pack-en.json` are derivative databases of the Open Food Facts database.
They are made available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Individual contents of the database are under the
[Database Contents License (DbCL) 1.0](https://opendatacommons.org/licenses/dbcl/1-0/).
If you publicly use this database or a database derived from it, you must
attribute Open Food Facts and keep it under the ODbL (share-alike), as the
licence requires. The script that produces them is `tools/build_offline_pack.py`
in this repository.
