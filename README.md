# Unsana geodata

Offline boundary data of the Unsana app. The app uses it to tell, without a network, which country, timezone and city a position is in.

This repository holds the two datasets as the app bundles them, their licenses and attribution, and the scripts that build them from pinned public sources. It does not hold the app.

Two of the sources are under the Open Database License, which asks that a database derived from them be offered to the public. This repository is that offer.

## Downloads

Publication `2026-10-10`:

| File | Contents | Version | Size | SHA-256 |
| --- | --- | --- | --- | --- |
| [`assets/data/geo.db`](https://github.com/OusamaJamalEddin/unsana-geodata/raw/2026-10-10/assets/data/geo.db) | Country, territory and timezone boundaries, and reviewed areas | `tbb-2025c-cgaz-2024-04-20`, build revision 1 | 7,032,832 bytes | `206f75bc510098452667e007541c3b9bbaab88664f432608b2e93ba7fc715c54` |
| [`assets/data/city_boundaries.db`](https://github.com/OusamaJamalEddin/unsana-geodata/raw/2026-10-10/assets/data/city_boundaries.db) | Administrative areas that each stand for one city | `overture-divisions-2026-09-23.1`, build revision 3 | 16,150,528 bytes | `46096e620a366dc9bd5b24cf815a77d08038ef7c126d7d905581d396bd292f94` |

The links give the files themselves. The files are stored with Git LFS: a clone made without Git LFS holds small pointer files in their place. Install Git LFS and run `git lfs pull`, or use the links.

To check a download, compare it with `SHA256SUMS`:

    sha256sum -c SHA256SUMS

Both files are SQLite databases. Each describes itself in its `dataset_info` table: sources, versions, checksums of the inputs, licenses and build settings. The format is in [`tool/geo/FORMAT.txt`](tool/geo/FORMAT.txt).

## Licenses and attribution

The full notices are in [`tool/geo/NOTICE.md`](tool/geo/NOTICE.md). In short:

| Data | Source and version | License |
| --- | --- | --- |
| `geo.db`, timezone boundaries | timezone-boundary-builder 2025c, from OpenStreetMap. © OpenStreetMap contributors | ODbL 1.0 |
| `geo.db`, country boundaries | geoBoundaries CGAZ ADM0, release of 2024-04-20 | CC BY 4.0 |
| `geo.db`, dependent territories | Natural Earth 5.1.2 | Public domain |
| `geo.db`, reviewed areas | Prepared for Unsana from OpenStreetMap, Natural Earth and geoBoundaries | As their sources: ODbL 1.0, public domain, CC BY 4.0 |
| `geo.db`, country names and codes | GeoNames | CC BY 4.0 |
| `city_boundaries.db`, areas | Overture Maps, divisions theme, release 2026-09-23.1. © OpenStreetMap contributors, Overture Maps Foundation. Parts come from geoBoundaries, Esri Community Maps contributors and Land Information New Zealand | ODbL 1.0; those parts CC BY 4.0 |
| `city_boundaries.db`, the city each area stands for | GeoNames | CC BY 4.0 |

The timezone layer of `geo.db` and all of `city_boundaries.db` are derivative databases and are offered under the Open Database License 1.0, https://opendatacommons.org/licenses/odbl/1-0/. The other layers keep the license of their source.

No software license has been chosen for the scripts yet. They are published so that the datasets can be checked and rebuilt.

## What was changed

- `geo.db`: boundaries are simplified at 0.005 degrees and coordinates rounded to 0.00001 degree. A place that two timezones share is stored once under both. Reviewed timezone areas and reviewed areas are added. Each country polygon carries the country code Unsana displays.
- `city_boundaries.db`: only land areas of four levels are read. An area is kept when it stands for one city of the app's city list, by the rules in `tool/geo/city_area_rules.py`. Each kept outline is simplified by 0.001 degree and rounded to 0.00001 degree. The attributes of the source are left out and only each area's id is kept. A small table marks the areas that are Saudi governorates.

The rules and their reasons are in `tool/geo/FORMAT.txt` and in the scripts.

## Rebuild

Python 3.13 with the packages of `tool/geo/requirements.txt`. From the repository root:

    pip install -r tool/geo/requirements.txt
    python tool/geo/fetch_geo_sources.py          # downloads the pinned inputs and checks them
    python tool/geo/fetch_overture_divisions.py   # copies the pinned columns of the Overture release, 3.7 GB
    python tool/geo/build_geo.py                  # writes build/datasets/geo/geo.db
    python tool/geo/build_city_boundaries.py      # writes build/datasets/citybounds/city_boundaries.db

Every input is pinned by version, size and SHA-256 in `tool/geo/geo_sources.lock.json`, and a build stops on an input that differs. Two runs give the same bytes, so a rebuilt file must have the checksum above.

Paths are the same as in the app's repository, so the scripts run unchanged. The `dart run tool/datasets/...` steps that the scripts mention record a dataset in the app and are not needed here. The scripts also mention documents of the app's repository, which is not public.

### Inputs that are not in this repository

Some inputs come from GeoNames, which publishes its files without versions and changes them daily, so they cannot be downloaded again as they were:

- `build/datasets/geonames/countryInfo.txt`, retrieved 2026-09-23, SHA-256 `93bafc525813f22e4711ff9ed6d626343094ce48c26388dc7c49189b3d7d5512`. `build_geo.py` reads it.
- `assets/data/cities.db`, the app's city list, version `geonames-2026-09-23`, SHA-256 `5e7c750dbc8a9ec4f914ebf2cb7fa8b224fd0d1b1af6bcf9b5e6e67022ace610`, and its entry in the app's `assets/data/datasets.json`. `build_city_boundaries.py` reads them.

They are not published here yet. Until they are, the datasets can be downloaded, read and checked from this repository, but not rebuilt from it alone.

Overture Maps keeps a release online for a limited time. After that, `fetch_overture_divisions.py` can no longer copy the pinned columns.

## Tests

    python -m unittest discover -s tool/geo -p "test_*.py"

## What the data is not

A boundary here is an administrative territory or a zone as its source draws it. It is not an official limit, and it implies no opinion of Unsana or of the sources on the status of any territory.

`city_boundaries.db` identifies a city only where the source has one area that stands for that city alone: 23,942 of the 45,204 cities of 10,000 people or more in the app's list. For any other place the app asks the user to select the city.
