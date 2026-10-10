# Notices for geo.db

For the dataset built by `build_geo.py` and bundled in the app as `assets/data/geo.db` since 2026-10-06, when the timezone boundary source was approved. Counsel has not reviewed this text. **Published since 2026-10-10** at https://github.com/OusamaJamalEddin/unsana-geodata, with the scripts that build it, by the owner's authorization of that day (DEC-052). That repository is the public download that the Open Database License calls for. The same text is meant for the app's licenses screen (Phase 8).

`geo.db` is a collection of four layers from different sources. Each layer keeps its own license. The file records each source's name, version, checksum and license in its `dataset_info` table.

## Timezone boundaries (layer 2)

Contains information from timezone-boundary-builder and OpenStreetMap, which is made available here under the Open Database License (ODbL) 1.0.

- Source: timezone-boundary-builder, release 2025c, "timezones-with-oceans", https://github.com/evansiroky/timezone-boundary-builder
- © OpenStreetMap contributors, https://www.openstreetmap.org/copyright
- License: Open Database License 1.0, https://opendatacommons.org/licenses/odbl/1-0/
- Changes made: boundaries simplified at 0.005 degrees; coordinates rounded to 0.00001 degree; a place that two zones share is stored once under both zones; seven reviewed timezone areas are added on top (next section).
- This layer is a derivative database and is offered under the ODbL 1.0. It can be rebuilt from the release named above with `build_geo.py`, which is published with it.

## Reviewed timezone areas (layer 2, rows marked "reviewed:")

Seven areas prepared for Unsana, in `reviewed_timezone_areas.geojson` beside this file. Each adds one candidate zone where the reviewed city data keeps another clock than the boundary source gives. They are not part of timezone-boundary-builder or OpenStreetMap.

- South Ossetia: outline from Natural Earth, disputed areas, version 5.1.2. Public domain.
- Six circles of 2 to 4 km around the bundled point of a city, drawn for Unsana.

## Country boundaries (layer 0)

- Source: geoBoundaries CGAZ ADM0, release of 2024-04-20, https://www.geoboundaries.org
- Runfola, D. et al. (2020), geoBoundaries: A global database of political administrative boundaries. PLoS ONE 15(4): e0231866.
- License: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/
- Its country polygons come from the Large Scale International Boundaries of the U.S. Department of State, Office of the Geographer. This use is not endorsed by the U.S. Government.
- Changes made: boundaries simplified at 0.005 degrees; coordinates rounded to 0.00001 degree; each polygon given the country code Unsana displays. Unsana's display choices are its own and are not those of the sources.

## Dependent territories (layer 1)

Made with Natural Earth, 10m admin 0 countries, version 5.1.2. Public domain. https://www.naturalearthdata.com

## Reviewed areas (layer 3)

Outlines prepared for Unsana from three sources:

- the 1949 demilitarized zone, from OpenStreetMap relation 16143349: © OpenStreetMap contributors, ODbL 1.0;
- disputed areas of Natural Earth, version 5.1.2: public domain;
- two polygons of geoBoundaries CGAZ ADM0: CC BY 4.0.

`reviewed_areas_draft.geojson` beside this file is the working file they are taken from. It carries the same notices.

## Country names and codes

GeoNames `countryInfo.txt`, https://www.geonames.org, CC BY 4.0.

# Notices for city_boundaries.db

For the dataset built by `build_city_boundaries.py` and bundled in the app as `assets/data/city_boundaries.db`. Its contents were replaced on 2026-10-10: it held urban centers of the GHS Urban Centre Database from 2026-10-09 and holds administrative areas of Overture Maps since. Counsel has not reviewed this text. **Published since 2026-10-10** at https://github.com/OusamaJamalEddin/unsana-geodata, with the scripts that build it, by the owner's authorization of that day (DEC-052). That repository is the public download that the Open Database License calls for. The same text is meant for the app's licenses screen (Phase 8).

## City boundaries

Contains information from Overture Maps and OpenStreetMap, which is made available here under the Open Database License (ODbL) 1.0.

- Source: Overture Maps Foundation, divisions theme (`division_area` and `division`), release 2026-09-23.1, https://overturemaps.org
- © OpenStreetMap contributors, Overture Maps Foundation. https://www.openstreetmap.org/copyright
- License of the theme: Open Database License 1.0, https://opendatacommons.org/licenses/odbl/1-0/
- The theme also holds areas from geoBoundaries (https://www.geoboundaries.org), Esri Community Maps contributors and Land Information New Zealand, each available under CC BY 4.0, https://creativecommons.org/licenses/by/4.0/
- Changes made: only land areas of four levels are read (locality, localadmin, county, region); an area is kept when it stands for one city of the GeoNames city list bundled with Unsana, by the rules in `FORMAT.txt` and `city_area_rules.py`; each kept outline is simplified by 0.001 degree and its coordinates rounded to 0.00001 degree; the attributes of the source are not included, only each area's id; a small table records which kept areas are Saudi governorates, so that they are named as governorates.
- This dataset is a derivative database and is offered under the ODbL 1.0. It can be rebuilt from the release named above with `fetch_overture_divisions.py` and `build_city_boundaries.py`, which are published with it.
- An area is an administrative territory as its source draws it. It is not an official city limit, and it implies no opinion of Unsana or of the sources on the status of any territory. Unsana uses it to tell which city a position is in, and the user can always select a city by hand instead.

## Open points on this source, not decided here

- The share-alike duty of the ODbL applies to this database as it does to the timezone layer of `geo.db`: when the app is distributed, the database must be offered under the ODbL with the notices above. Since 2026-10-10 the offer is the public repository named above, and the app's About section will link to it (Phase 8). A rebuilt dataset must be published there before it ships; a test of the app compares the bundled files with the record of the publication.
- The GeoNames files that the two builds read are not in that repository yet. Until they are, the datasets can be downloaded and checked there but not rebuilt from it alone.
- The areas of geoBoundaries inside the theme keep the license of their own primary source per country, which geoBoundaries lists layer by layer. Overture publishes them as CC BY 4.0. Whether a kept area comes from such a layer is recorded in the source, not in this file.
- Overture keeps a release in its public bucket for a limited time. The copies this build reads are pinned by checksum in `geo_sources.lock.json`. A later rebuild from scratch needs the release to be still online, or the pinned copies.

## City labels

The city an area stands for is a city of GeoNames, https://www.geonames.org, CC BY 4.0, as bundled in `cities.db`.
