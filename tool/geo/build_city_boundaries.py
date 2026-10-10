"""Builds city_boundaries.db, the offline city boundaries (P01-71, DEC-049 as
corrected on 2026-10-10).

Run from the repository root, with the Python environment of requirements.txt:

    python tool/geo/fetch_overture_divisions.py
    python tool/geo/build_city_boundaries.py [output path]
                                  (default: build/datasets/citybounds/city_boundaries.db)
    dart run tool/datasets/record_city_boundaries.dart
                                  (copies it into the app bundle and records it)

The source is the divisions theme of Overture Maps (ODbL): the administrative
areas of the world at several levels, most of them from OpenStreetMap. No
level means "city" in every country, so the build does not fix one. For each
bundled city of MIN_POPULATION people or more it takes the smallest area that
holds the city's position and carries its name, and it refuses an area that
holds another city or town. The rules, with their reasons, are in
city_area_rules.py.

What the build does
  1. Checks the pinned copies of the source and the bundled cities.db by
     SHA-256.
  2. Reads the land areas of the four levels and, for each bundled city
     inside one, compares the names (city_area_rules.same_name).
  3. Chooses each city's area and accepts or refuses it
     (city_area_rules.smallest and is_accepted). An area that several cities
     name is labeled with the most populous one. An accepted area inside a
     larger accepted area is left out when it is a part of that city
     (city_area_rules.is_place_of_its_own).
  4. Simplifies each accepted outline by TOLERANCE_DEGREES. The outline is
     kept as published where that would put the city itself outside. Nothing
     is repaired, merged or redrawn.
  5. Writes the polygons in the ring encoding of geo.db (FORMAT.txt), the
     smallest area first, and counts how many bundled cities the file
     identifies.
  6. Records which accepted areas are named as a unit and not as their city
     (city_area_rules.area_kind): the Saudi governorates.

A city without an accepted area has no row. The app answers "city not
identified" there and offers the manual selection; it never looks for a
nearest city.

Reads only. Two runs give the same bytes: rows are written in one fixed
sorted order and nothing depends on the clock. A list of what was decided for
every city is written beside the output for review.
"""
import collections
import csv
import hashlib
import json
import os
import sqlite3
import sys

import numpy as np
import shapely

import city_area_rules as rules
import geo_sources
from build_geo import BuildError, SCALE, encode_rings, log, polygon_parts, polygon_rings, sha256_of
from city_area_rules import Candidate, City

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
DEFAULT_OUTPUT = os.path.join(ROOT, 'build', 'datasets', 'citybounds', 'city_boundaries.db')

FORMAT_VERSION = 2
SOURCE_KEY = 'overture_divisions'
SOURCE_NAME = 'Overture Maps, divisions theme (division_area and division)'
PLACES_KEY = 'overture_division'
AREA_KEYS = tuple(f'overture_division_area_{number:02d}' for number in range(8))

CITIES_PATH = 'assets/data/cities.db'
MANIFEST_PATH = 'assets/data/datasets.json'
CITIES_DATASET_ID = 'cities'

# A bundled city with fewer people gets no boundary of its own.
MIN_POPULATION = 10000
# About 110 m on the ground. Each outline is simplified on its own.
TOLERANCE_DEGREES = 0.001
BATCH_ROWS = 2000

OUTCOME_IDENTIFIED = 'identified'
OUTCOME_NO_AREA = 'no_area_of_its_name'
OUTCOME_OTHER_PLACES = 'area_holds_another_city_or_town'
OUTCOME_NAMED_BY_LARGER_CITY = 'area_named_by_a_larger_city'
OUTCOME_PART_OF_LARGER_CITY = 'part_of_a_larger_city'
OUTCOMES = (OUTCOME_IDENTIFIED, OUTCOME_NO_AREA, OUTCOME_OTHER_PLACES, OUTCOME_NAMED_BY_LARGER_CITY,
            OUTCOME_PART_OF_LARGER_CITY)

IDENTIFICATION_POPULATIONS = (0, 10000, 50000, 100000, 500000, 1000000)

SCHEMA = (
    'CREATE TABLE city_areas ('
    'area_id INTEGER NOT NULL PRIMARY KEY, '
    'city_id INTEGER NOT NULL CHECK (city_id > 0), '
    'source_code TEXT NOT NULL, '
    'min_lat INTEGER NOT NULL, '
    'min_lon INTEGER NOT NULL, '
    'max_lat INTEGER NOT NULL, '
    'max_lon INTEGER NOT NULL, '
    'rings BLOB NOT NULL)',
    'CREATE INDEX city_areas_by_box ON city_areas (min_lat, max_lat, min_lon, max_lon)',
    'CREATE TABLE area_kinds (city_id INTEGER NOT NULL PRIMARY KEY, kind TEXT NOT NULL) WITHOUT ROWID',
    'CREATE TABLE dataset_info (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID',
)

# ?1 is an area file and ?2 the places file, which says what kind of place an area belongs to.
AREAS_SQL = (
    'SELECT area.id, area.subtype, area.country, area.names."primary", area.names.common[\'en\'], '
    'area.names.common[\'ar\'], [rule.value for rule in area.names.rules '
    'if rule.language IS NULL OR rule.language IN (\'en\', \'ar\')], area.geometry, place.class '
    'FROM read_parquet(?) area LEFT JOIN read_parquet(?) place ON place.id = area.division_id '
    'WHERE area.is_land AND area.subtype IN ({levels})'
).format(levels=', '.join(f"'{level}'" for level in rules.LEVELS))
PLACES_SQL = (
    'SELECT class, population, names."primary", names.common[\'en\'], names.common[\'ar\'], '
    '[rule.value for rule in names.rules if rule.language IS NULL OR rule.language IN (\'en\', \'ar\')], '
    'geometry FROM read_parquet(?) WHERE subtype = \'{place}\' AND class IN ({classes})'
).format(place=rules.PLACE_LEVEL, classes=', '.join(f"'{kind}'" for kind in rules.OTHER_PLACE_CLASSES))

# What the source says about an area that carries the name of a bundled city.
# place_class is what the source calls the place the area belongs to, None when it gives no class.
Area = collections.namedtuple('Area', 'source_id level country name names geometry area_km2 other_places '
                                      'tolerated_towns place_class')
# A city or town of the source: its names, class and population (None when the source gives none).
Place = collections.namedtuple('Place', 'names place_class population')
# An accepted area with the bundled city that labels it.
KeptArea = collections.namedtuple('KeptArea', 'source_id city_id area_km2 geometry')

_WGS84_RADIUS_METERS = 6378137.0
_WGS84_ECCENTRICITY = 0.0818191908426215


# ---------------------------------------------------------------- inputs

def root_path(relative):
    return os.path.join(ROOT, *relative.split('/'))


def source_entries():
    """The pinned copies of the source: the places file and the area files."""
    locked = geo_sources.load_lock()
    return locked[PLACES_KEY], [locked[key] for key in AREA_KEYS]


def combined_sha256(entries):
    """One checksum for the pinned copies together: of their keys and checksums, in key order."""
    lines = sorted(f'{entry["key"]} {entry["sha256"]}\n' for entry in entries)
    return hashlib.sha256(''.join(lines).encode('utf-8')).hexdigest()


def verify_sources(entries):
    """Stops unless every copy is the pinned one and cities.db is the bundled one. Returns its checksum and version."""
    for entry in entries:
        problem = geo_sources.problem_with(entry)
        if problem:
            raise BuildError(f'{entry["path"]}: {problem}. Run tool/geo/fetch_overture_divisions.py')
    with open(root_path(MANIFEST_PATH), encoding='utf-8') as stream:
        recorded = {dataset['datasetId']: dataset for dataset in json.load(stream)['datasets']}[CITIES_DATASET_ID]
    actual = sha256_of(root_path(CITIES_PATH))
    if recorded['checksum'] != f'sha256:{actual}':
        raise BuildError(f'{CITIES_PATH}: SHA-256 is {actual}, the manifest records {recorded["checksum"]}')
    log('the source copies and the bundled cities match their checksums')
    return actual, recorded['datasetVersion']


def read_cities():
    """The bundled cities by id, their (longitude, latitude) positions and, for the large enough, their names."""
    db = sqlite3.connect('file:' + root_path(CITIES_PATH).replace('\\', '/') + '?mode=ro', uri=True)
    try:
        rows = db.execute('SELECT city_id, COALESCE(population, 0), name_en, name_ar, longitude, latitude, '
                          'COALESCE(country_code, region_id) FROM cities ORDER BY city_id').fetchall()
        terms = collections.defaultdict(list)
        for city_id, term in db.execute('SELECT city_id, term FROM city_search_terms WHERE kind = 0'):
            terms[city_id].append(term)
    finally:
        db.close()
    cities = [City(row[0], row[1], row[2]) for row in rows]
    positions = np.array([(row[4], row[5]) for row in rows], dtype=np.float64)
    names = {index: rules.place_names([row[2], row[3], *terms[row[0]]])
             for index, row in enumerate(rows) if row[1] >= MIN_POPULATION}
    countries = [row[6] for row in rows]
    log('bundled cities read:', len(cities), 'of them with', MIN_POPULATION, 'people or more:', len(names))
    return cities, positions, names, countries


def connect():
    import duckdb  # Reads the Parquet copies; the tests of the rules do not need it.
    return duckdb.connect()


def read_places(connection, entry):
    """The cities and towns of the source, and a tree of their positions."""
    rows = connection.execute(PLACES_SQL, [geo_sources.path_of(entry).replace('\\', '/')]).fetchall()
    places = [Place(rules.place_names([row[2], row[3], row[4], *(row[5] or [])]), row[0], row[1]) for row in rows]
    points = shapely.from_wkb(np.array([row[6] for row in rows], dtype=object))
    log('cities and towns of the source read:', len(rows))
    return places, shapely.STRtree(points)


# ---------------------------------------------------------------- areas

def _equal_area(coordinates):
    """Longitude and latitude in degrees to meters on an equal-area map of the WGS84 ellipsoid."""
    e = _WGS84_ECCENTRICITY
    sine = np.sin(np.radians(coordinates[:, 1]))
    q = (1.0 - e * e) * (sine / (1.0 - e * e * sine * sine)
                         - (1.0 / (2.0 * e)) * np.log((1.0 - e * sine) / (1.0 + e * sine)))
    return np.column_stack([_WGS84_RADIUS_METERS * np.radians(coordinates[:, 0]), _WGS84_RADIUS_METERS * q / 2.0])


def area_km2(geometries):
    """The area of each geometry in square kilometers on the WGS84 ellipsoid."""
    return shapely.area(shapely.transform(np.array(geometries, dtype=object), _equal_area)) / 1e6


def other_places(inside, names, level, country):
    """Of the places [inside] an area: how many count against it, and how many are tolerated towns.

    A place that carries a name of the area is the area's own place and is
    left out.
    """
    others = [place for place in inside if not rules.same_name(names, place.names)]
    against = sum(1 for place in others
                  if rules.counts_against(level, country, place.place_class, place.population))
    return against, len(others) - against


def scan_file(connection, path, places_path, city_names, city_tree, tree_cities, places, place_tree, areas,
              by_city):
    """Adds the areas of one file that hold a bundled city and carry its name."""
    cursor = connection.execute(AREAS_SQL, [path.replace('\\', '/'), places_path.replace('\\', '/')])
    while True:
        batch = cursor.fetchmany(BATCH_ROWS)
        if not batch:
            break
        geometries = shapely.from_wkb(np.array([row[7] for row in batch], dtype=object))
        area_index, point_index = city_tree.query(geometries, predicate='intersects')
        names_of = {}
        for position, point in zip(area_index.tolist(), point_index.tolist()):
            row = batch[position]
            if position not in names_of:
                names_of[position] = rules.area_names([row[3], row[4], row[5], *(row[6] or [])])
            city = tree_cities[point]
            if not rules.same_name(names_of[position], city_names[city]):
                continue
            source_id = row[0]
            if source_id not in areas:
                geometry = geometries[position]
                inside = [places[place] for place in place_tree.query(geometry, predicate='intersects').tolist()]
                against, tolerated = other_places(inside, names_of[position], row[1], row[2])
                areas[source_id] = Area(source_id, row[1], row[2], row[4] or row[3] or '', names_of[position],
                                        geometry, float(area_km2([geometry])[0]), against, tolerated, row[8])
            by_city[city].append(source_id)


def scan_areas(connection, places_entry, area_entries, positions, city_names, places, place_tree):
    """Every area that holds a large enough bundled city and carries its name: by source id, and per city."""
    tree_cities = sorted(city_names)
    city_tree = shapely.STRtree(shapely.points(positions[tree_cities]))
    areas, by_city = {}, {city: [] for city in tree_cities}
    for entry in area_entries:
        scan_file(connection, geo_sources.path_of(entry), geo_sources.path_of(places_entry), city_names, city_tree,
                  tree_cities, places, place_tree, areas, by_city)
        log('areas read from', entry['key'], '- areas that carry the name of a city inside them so far:', len(areas))
    return areas, by_city


def candidate(area):
    return Candidate(area.source_id, area.level, area.area_km2, area.other_places)


def choose_areas(candidates_by_city):
    """For each city its chosen area (None when no area carries its name) and what that means for it."""
    chosen = {}
    for city, candidates in candidates_by_city.items():
        choice = rules.smallest(candidates)
        if choice is None:
            chosen[city] = (None, OUTCOME_NO_AREA)
        elif rules.is_accepted(choice.level, choice.other_places):
            chosen[city] = (choice, OUTCOME_IDENTIFIED)
        else:
            chosen[city] = (choice, OUTCOME_OTHER_PLACES)
    return chosen


def label_areas(chosen, cities):
    """The city index that labels each accepted area, and the final outcome of every city."""
    named_by = collections.defaultdict(list)
    for city, (choice, outcome) in chosen.items():
        if outcome == OUTCOME_IDENTIFIED:
            named_by[choice.source_id].append(city)
    owners = {source_id: min(indexes, key=lambda index: (-cities[index].population, cities[index].city_id))
              for source_id, indexes in named_by.items()}
    outcomes = {}
    for city, (choice, outcome) in chosen.items():
        lost = outcome == OUTCOME_IDENTIFIED and owners[choice.source_id] != city
        outcomes[city] = OUTCOME_NAMED_BY_LARGER_CITY if lost else outcome
    return owners, outcomes


def parts_of_larger_cities(areas, owners, positions):
    """The accepted areas, by source id, that are a part of a larger accepted area and not a place of their own.

    An area is inside a larger one when the position of the city that labels
    it lies in the larger one.
    """
    ordered = sorted(owners, key=lambda source_id: (areas[source_id].area_km2, source_id))
    tree = shapely.STRtree([areas[source_id].geometry for source_id in ordered])
    labels = shapely.points(np.array([positions[owners[source_id]] for source_id in ordered]))
    inner_index, outer_index = tree.query(labels, predicate='intersects')
    parts = set()
    for inner, outer in zip(inner_index.tolist(), outer_index.tolist()):
        area = areas[ordered[inner]]
        if outer > inner and not rules.is_place_of_its_own(area.level, area.place_class):
            parts.add(area.source_id)
    return parts


def without_parts(owners, outcomes, parts):
    """The labels and outcomes once the parts of larger cities are left out."""
    kept = {source_id: city for source_id, city in owners.items() if source_id not in parts}
    left_out = {owners[source_id] for source_id in parts}
    return kept, {city: OUTCOME_PART_OF_LARGER_CITY if city in left_out else outcome
                  for city, outcome in outcomes.items()}


def simplified(geometry, longitude, latitude):
    """The outline simplified by TOLERANCE_DEGREES, and whether the published one was kept instead.

    The published outline is kept when the simplified one no longer holds the
    city it stands for.
    """
    simple = shapely.simplify(geometry, TOLERANCE_DEGREES, preserve_topology=True)
    if shapely.is_empty(simple) or not shapely.intersects_xy(simple, longitude, latitude):
        return geometry, True
    return simple, False


def kept_areas(areas, owners, cities, positions):
    """The accepted areas with their label city, simplified, and how many kept their published outline."""
    kept, published = [], 0
    for source_id in sorted(owners):
        city = owners[source_id]
        geometry, kept_published = simplified(areas[source_id].geometry, *positions[city])
        published += kept_published
        kept.append(KeptArea(source_id, cities[city].city_id, areas[source_id].area_km2, geometry))
    return kept, published


# ---------------------------------------------------------------- output

def file_order(kept):
    """The areas in the order of the file: the smallest first; among equals, by source id."""
    return sorted(kept, key=lambda area: (area.area_km2, area.source_id))


def area_rows(kept, drops):
    """The city_areas rows without their ids, each followed by its vertex count, in file order."""
    rows = []
    for area in file_order(kept):
        parts = []
        for polygon in polygon_parts(area.geometry):
            rings, dropped = polygon_rings(polygon)
            drops['rings'] += dropped
            if not rings:
                drops['polygons'] += 1
                continue
            points = np.vstack(rings)
            parts.append((area.city_id, area.source_id, int(points[:, 1].min()), int(points[:, 0].min()),
                          int(points[:, 1].max()), int(points[:, 0].max()), encode_rings(rings), len(points)))
        rows.extend(sorted(parts, key=lambda row: row[2:7]))
    return rows


def first_areas(positions, ordered):
    """For each position inside an area: the place, in [ordered], of the first area that holds it."""
    tree = shapely.STRtree([area.geometry for area in ordered])
    point_index, area_index = tree.query(shapely.points(positions), predicate='intersects')
    first = {}
    for point, area in zip(point_index.tolist(), area_index.tolist()):
        if point not in first or area < first[point]:
            first[point] = area
    return first


def area_kind_rows(kept, areas):
    """The area_kinds rows: (city_id, kind) for each kept area that is named as a unit, by city_id."""
    rows = []
    for area in kept:
        kind = rules.area_kind(areas[area.source_id].level, areas[area.source_id].country)
        if kind is not None:
            rows.append((area.city_id, kind))
    return sorted(rows)


def identification(cities, positions, kept, populations=IDENTIFICATION_POPULATIONS):
    """How many bundled cities lie in an area of the file, and how many of them it identifies.

    A city is identified when the first area of the file that holds its
    position is labeled with that city. A city inside the area of another
    city is inside an area and not identified.
    """
    ordered = file_order(kept)
    first = first_areas(positions, ordered)
    facts = {}
    for population in populations:
        counted = [index for index, city in enumerate(cities) if city.population >= population]
        inside = [index for index in counted if index in first]
        identified = [index for index in inside if ordered[first[index]].city_id == cities[index].city_id]
        facts[f'identification.population_{population}.cities'] = str(len(counted))
        facts[f'identification.population_{population}.inside'] = str(len(inside))
        facts[f'identification.population_{population}.identified'] = str(len(identified))
    return facts


def dataset_info(entries, cities_sha256, cities_version, areas, owners, outcomes, rows, kinds, drops, published):
    levels = collections.Counter(areas[source_id].level for source_id in owners)
    counted = collections.Counter(outcomes.values())
    import duckdb
    info = {
        'format_version': str(FORMAT_VERSION),
        'coordinate_units_per_degree': str(SCALE),
        'levels': ', '.join(rules.LEVELS),
        'min_population': str(MIN_POPULATION),
        'selection_rule': 'for each bundled city of min_population people or more: the smallest land area of the '
                          'levels that holds its position and carries its name; a unit above locality level is '
                          'refused when the source lists another city or town inside it, but for the towns of '
                          'country_rules; an area several cities name is labeled with the most populous; an '
                          'accepted area inside a larger accepted area is left out unless it is the locality of a '
                          'place the source classes as a city or town',
        'country_rules': json.dumps(rules.TOLERATED_TOWNS, sort_keys=True),
        'country_rules_meaning': 'country, then level: inside a unit of that level a town of fewer people than the '
                                 'number, or of unknown size, does not refuse the unit; another city always does',
        'simplification_tolerance_degrees': repr(TOLERANCE_DEGREES),
        'simplification_method': 'shapely.simplify with preserve_topology, each outline on its own; the published '
                                 'outline is kept where the simplified one would not hold the city',
        'row_order': 'area of the unit in square kilometers, source_code, min_lat, min_lon, max_lat, max_lon, rings',
        'builder.python': '.'.join(str(part) for part in sys.version_info[:3]),
        'builder.shapely': shapely.__version__,
        'builder.geos': shapely.geos_version_string,
        'builder.numpy': np.__version__,
        'builder.duckdb': duckdb.__version__,
        f'source.{SOURCE_KEY}.name': SOURCE_NAME,
        f'source.{SOURCE_KEY}.version': entries[0]['version'],
        f'source.{SOURCE_KEY}.license': entries[0]['license'],
        f'source.{SOURCE_KEY}.sha256': combined_sha256(entries),
        f'source.{SOURCE_KEY}.files': json.dumps({entry['key']: entry['sha256'] for entry in entries}, sort_keys=True),
        'source.cities.name': 'Unsana cities.db',
        'source.cities.version': cities_version,
        'source.cities.sha256': cities_sha256,
        'source.cities.path': CITIES_PATH,
        'areas.with_the_name_of_a_city': str(len(areas)),
        'areas.kept': str(len(owners)),
        'areas.kept_as_published': str(published),
        'area_kinds': json.dumps(rules.AREA_KINDS, sort_keys=True),
        'area_kinds_meaning': 'country, then level: an accepted area of that level is named as the kind given, not as '
                              'its city; the table area_kinds lists the cities this applies to',
        'areas.kept_with_tolerated_towns': str(sum(1 for source_id in owners if areas[source_id].tolerated_towns
                                                   and areas[source_id].level != rules.PLACE_LEVEL)),
        'rows': str(len(rows)),
        'vertices': str(sum(row[7] for row in rows)),
        'cities': str(len({row[0] for row in rows})),
        'dropped_rings': str(drops['rings']),
        'dropped_polygons': str(drops['polygons']),
    }
    for level in rules.LEVELS:
        info[f'areas.kept.{level}'] = str(levels[level])
    for outcome in OUTCOMES:
        info[f'cities.{outcome}'] = str(counted[outcome])
    for kind, count in sorted(collections.Counter(kind for _, kind in kinds).items()):
        info[f'areas.named.{kind}'] = str(count)
    return info


def write_database(path, rows, kinds, info):
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute('PRAGMA page_size = 4096')
    db.execute('BEGIN')
    for statement in SCHEMA:
        db.execute(statement)
    db.executemany('INSERT INTO city_areas VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                   [(area_id, *row[:7]) for area_id, row in enumerate(rows, start=1)])
    db.executemany('INSERT INTO area_kinds VALUES (?, ?)', kinds)
    db.executemany('INSERT INTO dataset_info VALUES (?, ?)', sorted(info.items()))
    db.execute(f'PRAGMA user_version = {FORMAT_VERSION}')
    db.execute('COMMIT')
    db.execute('VACUUM')
    db.close()


def write_decisions(path, cities, countries, chosen, outcomes, areas):
    """What was decided for every bundled city of MIN_POPULATION people or more, for review: one line per city."""
    with open(path, 'w', encoding='utf-8', newline='\n') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['city_id', 'city_name', 'country', 'population', 'outcome', 'level', 'source_id',
                         'area_name', 'area_km2', 'other_cities_and_towns', 'tolerated_towns', 'place_class',
                         'area_kind'])
        for city in sorted(chosen, key=lambda index: cities[index].city_id):
            choice = chosen[city][0]
            area = areas[choice.source_id] if choice else None
            fields = ([area.level, area.source_id, area.name, f'{area.area_km2:.1f}', area.other_places,
                       area.tolerated_towns, area.place_class or '',
                       (rules.area_kind(area.level, area.country) or '') if outcomes[city] == OUTCOME_IDENTIFIED
                       else ''] if area else [''] * 8)
            writer.writerow([cities[city].city_id, cities[city].name, countries[city], cities[city].population,
                             outcomes[city], *fields])


def write_merged(path, cities, countries, positions, kept, areas):
    """The known merges, for review: every bundled city of MIN_POPULATION people or more that the file gives the
    name of another city, because the first area that holds it is that city's."""
    ordered = file_order(kept)
    first = first_areas(positions, ordered)
    by_id = {city.city_id: city for city in cities}
    with open(path, 'w', encoding='utf-8', newline='\n') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['city_id', 'city_name', 'country', 'population', 'named_as_city_id', 'named_as',
                         'named_as_population', 'level', 'area_km2'])
        for index in sorted(first, key=lambda index: (-cities[index].population, cities[index].city_id)):
            area = ordered[first[index]]
            if cities[index].population < MIN_POPULATION or area.city_id == cities[index].city_id:
                continue
            owner = by_id[area.city_id]
            writer.writerow([cities[index].city_id, cities[index].name, countries[index], cities[index].population,
                             owner.city_id, owner.name, owner.population, areas[area.source_id].level,
                             f'{area.area_km2:.1f}'])


def build(path):
    places_entry, area_entries = source_entries()
    entries = [places_entry, *area_entries]
    cities_sha256, cities_version = verify_sources(entries)
    cities, positions, city_names, countries = read_cities()
    connection = connect()
    places, place_tree = read_places(connection, places_entry)
    areas, by_city = scan_areas(connection, places_entry, area_entries, positions, city_names, places, place_tree)
    connection.close()
    chosen = choose_areas({city: [candidate(areas[source_id]) for source_id in source_ids]
                           for city, source_ids in by_city.items()})
    owners, outcomes = label_areas(chosen, cities)
    owners, outcomes = without_parts(owners, outcomes, parts_of_larger_cities(areas, owners, positions))
    kept, published = kept_areas(areas, owners, cities, positions)
    drops = collections.Counter()
    rows = area_rows(kept, drops)
    kinds = area_kind_rows(kept, areas)
    info = dataset_info(entries, cities_sha256, cities_version, areas, owners, outcomes, rows, kinds, drops,
                        published)
    info.update(identification(cities, positions, kept))
    write_database(path, rows, kinds, info)
    write_decisions(os.path.splitext(path)[0] + '_decisions.csv', cities, countries, chosen, outcomes, areas)
    write_merged(os.path.splitext(path)[0] + '_merged.csv', cities, countries, positions, kept, areas)
    log('wrote', path, os.path.getsize(path), 'bytes, SHA-256', sha256_of(path))
    for key in sorted(info):
        if key.startswith(('areas.', 'cities', 'identification.', 'rows', 'vertices', 'dropped')):
            log('  ', key, '=', info[key])


def main():
    path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        build(path)
    except (BuildError, geo_sources.SourceError) as error:
        sys.exit(f'BUILD FAILED: {error}')


if __name__ == '__main__':
    main()
