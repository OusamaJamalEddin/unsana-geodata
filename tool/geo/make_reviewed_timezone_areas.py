"""Writes reviewed_timezone_areas.geojson, the places where the reviewed city
data and the timezone boundary data give different clocks (P01-65, DEC-013).

Run from the repository root:

    python tool/geo/make_reviewed_timezone_areas.py

Each area adds one more candidate zone on top of the boundary data. It forces
nothing: the app's overlap rule then lets the reviewed cities around the point
decide, and asks the user where they do not agree.

A circle is drawn around a reviewed city whose observed clock differs from the
zone the boundary data gives at the city. South Ossetia is taken as drawn by
Natural Earth, because the evidence is for the region. Every area names the
evidence it rests on. An area is added only for a difference in the clock that
the review settled; a city whose bundled point merely lies across a border
gets none.

Reads the Natural Earth disputed areas file from the ignored build folder and
checks it against the SHA-256 pinned in geo_sources.lock.json. Python 3 standard library only. Two runs
give the same bytes.
"""
import hashlib
import json
import math
import os
import sys

import geo_sources

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(HERE, 'reviewed_timezone_areas.geojson')

# Pinned in geo_sources.lock.json; fetch_geo_sources.py downloads it.
_DISPUTED = geo_sources.load_lock()['natural_earth_disputed']
NATURAL_EARTH = _DISPUTED['path']
NATURAL_EARTH_SHA256 = _DISPUTED['sha256']

KM_PER_DEGREE = 111.195
CIRCLE_POINTS = 48
REVIEW = 'docs/03-implementation/phases/phase-01/evidence/dec-013/'

# id, zone added, city id, latitude, longitude, radius in km, reference.
CIRCLES = [
    ('fort-pierre', 'America/Chicago', 5764864, 44.35359, -100.37374, 3,
     'Fort Pierre, South Dakota, keeps Central time; the boundary data follows the legal line on the Missouri '
     'River and gives Mountain time. ' + REVIEW + 'timezone_review_manifest.csv, row 5764864: evidence strong, '
     'check complete. The radius covers the town only: how far west the practice extends is not established.'),
    ('araguaiana', 'America/Sao_Paulo', 3471834, -15.73389, -51.83139, 3,
     'Araguaiana, Mato Grosso, keeps Brasilia time; the boundary data gives the legal zone of the state. '
     + REVIEW + 'timezone_review_manifest.csv, row 3471834: evidence strong, check complete.'),
    ('mbamba-bay', 'Africa/Dar_es_Salaam', 878023, -11.28333, 34.76667, 4,
     'Tanzanian town on the shore of Lake Nyasa. The boundary data gives the lake to Malawi up to the shore, and '
     'the bundled point lies 2.35 km inside the lake polygon. ' + REVIEW + 'timezone_review_manifest.csv, row '
     '878023: Tanzania keeps one national time, check complete.'),
    ('manda', 'Africa/Dar_es_Salaam', 878087, -10.46667, 34.58333, 3,
     'Tanzanian town on the shore of Lake Nyasa; its bundled point lies 0.5 km inside the lake polygon. '
     + REVIEW + 'timezone_review_manifest.csv, row 878087: check complete.'),
    ('liuli', 'Africa/Dar_es_Salaam', 878242, -11.08333, 34.63333, 3,
     'Tanzanian town on the shore of Lake Nyasa; its bundled point lies 0.37 km inside the lake polygon. '
     + REVIEW + 'timezone_review_manifest.csv, row 878242: check complete.'),
    ('longwa', 'Asia/Kolkata', 13645768, 26.66172, 95.22004, 2,
     'Village on the India and Myanmar border. GeoNames and the unsimplified boundary data put its point in the '
     'Indian zone, on the line itself; the simplified boundary of geo.db puts it on the Myanmar side. Found by '
     'the consistency check of 2026-10-06 (' + REVIEW.replace('dec-013/', '')
     + 'dec-013-gps-lookup-2026-10-06.md).'),
]

# id, zone added, Natural Earth BRK_NAME, reference.
OUTLINES = [
    ('south-ossetia', 'Europe/Moscow', 'South Ossetia',
     'South Ossetia keeps Moscow time; the boundary data gives Asia/Tbilisi for all of it. ' + REVIEW
     + 'timezone_review_additional_manifest.csv, region South Ossetia: 5 cities applied with strong evidence, and '
     'Akhalgori left unsettled. Outline: Natural Earth 5.1.2 disputed areas, public domain.'),
]


def circle(latitude, longitude, radius_km):
    """A closed ring of (lon, lat) around the point, counterclockwise."""
    ring = []
    for step in range(CIRCLE_POINTS):
        angle = 2 * math.pi * step / CIRCLE_POINTS
        d_lat = radius_km / KM_PER_DEGREE * math.sin(angle)
        d_lon = radius_km / (KM_PER_DEGREE * math.cos(math.radians(latitude))) * math.cos(angle)
        ring.append([round(longitude + d_lon, 5), round(latitude + d_lat, 5)])
    return ring + [ring[0]]


def natural_earth_outlines():
    path = os.path.join(ROOT, *NATURAL_EARTH.split('/'))
    data = open(path, 'rb').read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != NATURAL_EARTH_SHA256:
        sys.exit(f'{NATURAL_EARTH}: SHA-256 is {actual}, expected {NATURAL_EARTH_SHA256}')
    by_name = {}
    for feature in json.loads(data.decode('utf-8'))['features']:
        by_name.setdefault(feature['properties']['BRK_NAME'], []).append(feature['geometry'])
    return by_name


def feature(area_id, timezone, geometry, reference, geometry_source, city_id=None):
    properties = {'id': area_id, 'timezone': timezone, 'reference': reference, 'geometry_source': geometry_source}
    if city_id is not None:
        properties['city_id'] = city_id
    return {'type': 'Feature', 'properties': properties, 'geometry': geometry}


def main():
    features = []
    for area_id, timezone, city_id, latitude, longitude, radius_km, reference in CIRCLES:
        features.append(feature(
            area_id, timezone, {'type': 'Polygon', 'coordinates': [circle(latitude, longitude, radius_km)]},
            reference, f'{radius_km} km circle around the bundled point of city {city_id} (this study)', city_id))
    outlines = natural_earth_outlines()
    for area_id, timezone, name, reference in OUTLINES:
        geometries = outlines.get(name, [])
        if len(geometries) != 1:
            sys.exit(f'Natural Earth has {len(geometries)} outlines named {name}, expected 1')
        features.append(feature(area_id, timezone, geometries[0], reference,
                                'Natural Earth v5.1.2 disputed areas (public domain)'))
    features.sort(key=lambda item: item['properties']['id'])
    text = json.dumps({'type': 'FeatureCollection', 'features': features}, ensure_ascii=True, indent=1) + '\n'
    with open(OUT, 'w', encoding='utf-8', newline='\n') as out:
        out.write(text)
    print('wrote', os.path.relpath(OUT, ROOT).replace(os.sep, '/'), len(features), 'areas, SHA-256',
          hashlib.sha256(text.encode('utf-8')).hexdigest())


main()
