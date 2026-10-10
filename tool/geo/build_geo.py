"""Builds geo.db, the offline geo dataset, from pinned sources (P01-16, P01-65, DEC-013).

Run from the repository root:

    python tool/geo/build_geo.py [output path]      (default: build/datasets/geo/geo.db)
    dart run tool/datasets/record_geo.dart          (copies it into the app bundle and records it)

The timezone boundary source was approved on 2026-10-06. The downloads are
kept under the ignored build/datasets/; their origins are in
docs/03-implementation/source_inventory.md.

Needs Python 3.13 with numpy 2.5.3, shapely 2.1.2 (GEOS 3.13.1) and pyshp 3.1.6.
Reads only. Every input is checked against a SHA-256 constant first and the
build stops on a mismatch. Two runs give the same bytes: rows are inserted in
one fixed sorted order and nothing depends on the clock.

Layers
  0  country    geoBoundaries CGAZ ADM0, coverage-simplified at 0.005 degrees
  1  territory  Natural Earth 10m polygons of dependent territories, as published
  2  timezone   timezone-boundary-builder 2025c "with oceans", coverage-simplified
                at 0.005 degrees, overlaps kept (see timezone_pieces); and the
                reviewed timezone areas, as drawn, each of which adds one more
                candidate zone where the reviewed city data keeps another clock
  3  reviewed   reviewed areas from the draft areas file, as drawn

The file format is described in FORMAT.txt beside this script, and
lib/core/geo/geo_db_format.dart holds the same schema for the app's reader.
"""
import hashlib
import io
import json
import os
import sqlite3
import sys
import time
import zipfile
from collections import Counter

import numpy as np
import shapefile
import shapely
from shapely.geometry import shape

import geo_sources

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
DEFAULT_OUTPUT = os.path.join(ROOT, 'build', 'datasets', 'geo', 'geo.db')

FORMAT_VERSION = 1
SCALE = 100000
TOLERANCE_DEGREES = 0.005
# Zones sharing more than this area overlap on purpose and keep their shared part.
OVERLAP_MIN_AREA_DEG2 = 1e-9
# A vertex this close to a neighbor's border segment is taken to lie on it (source coordinates have 6 decimals).
VERTEX_MATCH_DEGREES = 2e-6

LAYER_COUNTRY, LAYER_TERRITORY, LAYER_TIMEZONE, LAYER_REVIEWED = 0, 1, 2, 3
LAYERS = (LAYER_COUNTRY, LAYER_TERRITORY, LAYER_TIMEZONE, LAYER_REVIEWED)

# The downloaded inputs are pinned in geo_sources.lock.json, which
# fetch_geo_sources.py also reads. The two files below them are committed.
_LOCKED = geo_sources.load_lock()


def _locked(key):
    return {field: _LOCKED[key][field] for field in ('path', 'sha256', 'name', 'version', 'license')}


SOURCES = {
    'cgaz': _locked('cgaz'),
    'geonames_country_info': _locked('geonames_country_info'),
    'natural_earth': _locked('natural_earth'),
    'tbb': _locked('tbb'),
    'reviewed_areas': {
        'path': 'tool/geo/reviewed_areas_draft.geojson',
        'sha256': 'f1cc9e61883f1bee93d0cf31deae2599315026a048b3ec84924d8239f9114be7',
        'name': 'Unsana draft reviewed areas (DEC-021 research of 2026-09-26)',
        'version': 'draft of 2026-09-26',
        'license': 'mixed: OpenStreetMap (ODbL 1.0), Natural Earth (public domain), CGAZ (attribution)',
    },
    'reviewed_timezone_areas': {
        'path': 'tool/geo/reviewed_timezone_areas.geojson',
        'sha256': '3aa66522405339b6890ef3ee6127da338588f1286fb96c5397e31c1de0559663',
        'name': 'Unsana reviewed timezone areas (DEC-013, written by make_reviewed_timezone_areas.py)',
        'version': 'reviewed 2026-10-06',
        'license': 'Natural Earth outline (public domain) and circles drawn for Unsana',
    },
}

CGAZ_MEMBER = 'geoBoundariesCGAZ_ADM0'
TBB_MEMBER = 'combined-with-oceans.json'

# CGAZ polygons without an ISO code that are displayed as Palestine (DEC-021).
CGAZ_PALESTINE_NAMES = ('Gaza Strip', "No Man's Land", 'West Bank')
CGAZ_ISRAEL_GROUP = 'ISR'
FORBIDDEN_CODE = 'IL'

# GeoNames codes with no coded CGAZ polygon that get no overlay: two retired
# codes; Palestine, which layer 0 covers through the mapping above; and the
# Falkland Islands, which have a CGAZ polygon of their own (uncoded, unresolved).
NOT_IN_OVERLAY = ('AN', 'CS', 'FK', 'PS')
# Territories that Natural Earth draws inside the feature of another country:
# code: (Natural Earth ADM0_A3, boxes (min_lon, min_lat, max_lon, max_lat) that hold all its parts).
TERRITORY_SPLITS = {
    'BQ': ('NLD', ((-69.0, 11.5, -62.5, 18.0),)),
    'BV': ('NOR', ((3.0, -55.0, 4.0, -54.0),)),
    'CC': ('IOA', ((96.0, -13.0, 97.5, -11.5),)),
    'CX': ('IOA', ((105.0, -11.0, 106.0, -10.0),)),
    'GF': ('FRA', ((-55.0, 2.0, -51.0, 6.0),)),
    'GP': ('FRA', ((-62.0, 15.7, -60.9, 16.6),)),
    'MQ': ('FRA', ((-61.3, 14.3, -60.7, 15.0),)),
    'RE': ('FRA', ((55.0, -21.5, 56.0, -20.7),)),
    'SJ': ('NOR', ((-10.0, 70.5, -7.0, 71.5), (9.0, 74.0, 35.0, 81.5))),
    'TK': ('NZL', ((-173.0, -10.0, -171.0, -8.0),)),
    'YT': ('FRA', ((44.9, -13.1, 45.4, -12.5),)),
}

# Reviewed areas taken from the draft file: id: (rule, region id, reference, English name or None for the file's).
REVIEWED_AREAS = {
    'G3-DMZ-NORTH': ('manualCitySelection', None, 'DEC-038, approved 2026-09-30', None),
    'G3-DMZ-CENTRAL': ('manualCitySelection', None, 'DEC-038, approved 2026-09-30', None),
    'G3-DMZ-SOUTH': ('manualCitySelection', None, 'DEC-038, approved 2026-09-30', None),
    'G4-SHEBAA': ('manualCitySelection', None, 'DEC-038, approved 2026-09-30', None),
    'G5-GHAJAR': ('manualCitySelection', None, 'DEC-038, approved 2026-09-30', None),
    'NE-Jammu_and_Kashmir': ('manualCitySelection', None, 'DEC-038, approved 2026-09-30', None),
    'NE-Azad_Kashmir': ('manualCitySelection', None, 'DEC-038, approved 2026-09-30', None),
    'NE-Gilgit-Baltistan': ('manualCitySelection', None, 'DEC-038, approved 2026-09-30', None),
    'NE-Shaksam_Valley': ('manualCitySelection', None, 'DEC-038, approved 2026-09-30', None),
    'CGAZ-111': ('neutralRegion', 'abyei', 'DEC-038, approved 2026-10-03', 'Abyei'),
    'CGAZ-125': ('neutralRegion', 'paracel-islands', 'DEC-038, approved 2026-10-03', 'Paracel Islands'),
}

SCHEMA = (
    """CREATE TABLE areas (
  area_id INTEGER NOT NULL PRIMARY KEY,
  layer INTEGER NOT NULL CHECK (layer IN (0, 1, 2, 3)),
  code TEXT,
  source_code TEXT NOT NULL,
  min_lat INTEGER NOT NULL, min_lon INTEGER NOT NULL,
  max_lat INTEGER NOT NULL, max_lon INTEGER NOT NULL,
  rings BLOB NOT NULL)""",
    """CREATE TABLE reviewed_areas (
  code TEXT NOT NULL PRIMARY KEY, name_en TEXT NOT NULL, rule TEXT NOT NULL,
  region_id TEXT, reference TEXT NOT NULL) WITHOUT ROWID""",
    'CREATE TABLE dataset_info (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID',
)
INDEX = 'CREATE INDEX areas_by_box ON areas (min_lat, max_lat, min_lon, max_lon)'

T0 = time.time()


def log(*parts):
    print(f'[{time.time() - T0:7.1f}s]', *parts, flush=True)


class BuildError(Exception):
    """The inputs or the result break a rule the dataset must keep."""


# ---------------------------------------------------------------- inputs

def source_path(key):
    return os.path.join(ROOT, *SOURCES[key]['path'].split('/'))


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_sources():
    for key in sorted(SOURCES):
        actual = sha256_of(source_path(key))
        if actual != SOURCES[key]['sha256']:
            raise BuildError(f'{key}: SHA-256 is {actual}, expected {SOURCES[key]["sha256"]}')
    log('all', len(SOURCES), 'source checksums match')


def read_country_info():
    """GeoNames countryInfo.txt as {ISO2: ISO3}."""
    codes = {}
    with open(source_path('geonames_country_info'), encoding='utf-8') as stream:
        for line in stream:
            if line.startswith('#') or not line.strip():
                continue
            fields = line.rstrip('\n').split('\t')
            codes[fields[0]] = fields[1]
    return codes


def read_cgaz():
    """CGAZ ADM0 as (records, geometries); a record is (shapeGroup, shapeType, shapeName)."""
    with zipfile.ZipFile(source_path('cgaz')) as archive:
        parts = {ext: io.BytesIO(archive.read(f'{CGAZ_MEMBER}.{ext}')) for ext in ('shp', 'dbf', 'shx')}
    reader = shapefile.Reader(shp=parts['shp'], dbf=parts['dbf'], shx=parts['shx'])
    records, geometries = [], []
    for item in reader.iterShapeRecords():
        records.append((item.record[0], item.record[1], item.record[2]))
        geometries.append(shape(item.shape.__geo_interface__))
    return records, geometries


def iter_geojson_features(text):
    """Yields the features of a GeoJSON text one at a time, to keep memory low."""
    decoder = json.JSONDecoder()
    position = text.index('[', text.index('"features"')) + 1
    while True:
        while text[position] in ' \r\n\t,':
            position += 1
        if text[position] == ']':
            return
        feature, position = decoder.raw_decode(text, position)
        yield feature


def read_tbb():
    """TBB zones as (IANA ids, geometries), in file order."""
    with zipfile.ZipFile(source_path('tbb')) as archive:
        text = archive.read(TBB_MEMBER).decode('utf-8')
    ids, geometries = [], []
    for feature in iter_geojson_features(text):
        ids.append(feature['properties']['tzid'])
        geometries.append(shape(feature['geometry']))
    return ids, geometries


def read_geojson(key):
    with open(source_path(key), encoding='utf-8') as stream:
        return json.load(stream)['features']


# ---------------------------------------------------------------- geometry helpers

def polygon_parts(geometry):
    """The Polygon parts of a geometry, in order."""
    if geometry.geom_type == 'Polygon':
        return [geometry]
    return [part for member in getattr(geometry, 'geoms', []) for part in polygon_parts(member)]


def polygonal_area(geometry):
    return sum(part.area for part in polygon_parts(geometry))


def simplify_coverage(geometries):
    """Simplifies polygons that tile without overlaps; each shared border moves once."""
    array = np.array(geometries, dtype=object)
    if not np.all(shapely.is_valid(array)):
        raise BuildError('a source polygon is not valid')
    if not shapely.coverage_is_valid(array):
        raise BuildError('the polygons do not form a valid coverage')
    return list(shapely.coverage_simplify(array, TOLERANCE_DEGREES))


# ---------------------------------------------------------------- layer 0

def country_code(record, iso3_to_iso2):
    """(displayed ISO2 code or None, source code) of one CGAZ polygon."""
    group, _, name = record
    if not group.isalpha():
        return ('PS' if name in CGAZ_PALESTINE_NAMES else None), name
    if group not in iso3_to_iso2:
        raise BuildError(f'CGAZ polygon {group} ({name}) has no ISO2 mapping')
    return ('PS' if group == CGAZ_ISRAEL_GROUP else iso3_to_iso2[group]), group


def check_country_codes(shapes):
    labels = {(code, source_code) for code, source_code, _ in shapes}
    if FORBIDDEN_CODE in {code for code, _ in labels}:
        raise BuildError(f'a country polygon maps to {FORBIDDEN_CODE}')
    for name in CGAZ_PALESTINE_NAMES + (CGAZ_ISRAEL_GROUP,):
        if ('PS', name) not in labels:
            raise BuildError(f'CGAZ polygon {name} was not found or is not mapped to PS')


def country_shapes(iso3_to_iso2):
    """Layer 0 as a list of (code, source code, geometry)."""
    records, geometries = read_cgaz()
    log('CGAZ polygons', len(records), 'vertices', int(shapely.get_num_coordinates(geometries).sum()))
    simplified = simplify_coverage(geometries)
    shapes = [(*country_code(record, iso3_to_iso2), geometry) for record, geometry in zip(records, simplified)]
    check_country_codes(shapes)
    return shapes


# ---------------------------------------------------------------- layer 1

def territory_codes(iso2_to_iso3, countries):
    """GeoNames codes with no coded CGAZ polygon, less the ones that get no overlay."""
    cgaz_groups = {source_code for code, source_code, _ in countries if code is not None}
    return sorted(iso2 for iso2, iso3 in iso2_to_iso3.items()
                  if iso3 not in cgaz_groups and iso2 not in NOT_IN_OVERLAY)


def natural_earth_index(features):
    """Natural Earth features by ISO_A2, and by ADM0_A3."""
    by_iso2, by_adm0 = {}, {}
    for feature in features:
        properties = feature['properties']
        by_adm0[properties['ADM0_A3']] = feature
        if properties['ISO_A2'] != '-99':
            by_iso2[properties['ISO_A2']] = feature
    return by_iso2, by_adm0


def inside_box(bounds, box):
    return box[0] <= bounds[0] and box[1] <= bounds[1] and bounds[2] <= box[2] and bounds[3] <= box[3]


def parts_inside_boxes(geometry, boxes):
    """Polygon parts lying wholly inside one of the (min_lon, min_lat, max_lon, max_lat) boxes."""
    return [part for part in polygon_parts(geometry) if any(inside_box(part.bounds, box) for box in boxes)]


def territory_shapes(codes):
    """Layer 1 as (code, Natural Earth ADM0_A3, geometry), plus the codes Natural Earth cannot supply."""
    by_iso2, by_adm0 = natural_earth_index(read_geojson('natural_earth'))
    shapes, missing = [], []
    for code in codes:
        if code in TERRITORY_SPLITS:
            adm0, boxes = TERRITORY_SPLITS[code]
            parts = parts_inside_boxes(shape(by_adm0[adm0]['geometry']), boxes)
            if not parts:
                raise BuildError(f'{code}: no Natural Earth part of {adm0} inside {boxes}')
            shapes.append((code, adm0, shapely.MultiPolygon(parts)))
        elif code in by_iso2:
            feature = by_iso2[code]
            shapes.append((code, feature['properties']['ADM0_A3'], shape(feature['geometry'])))
        else:
            missing.append(code)
    return shapes, missing


# ---------------------------------------------------------------- layer 2: shared vertices

def ring_table(array):
    """All ring coordinates, with the ring of each coordinate, the polygon of each ring and the geometry of each polygon."""
    polygons, polygon_owner = shapely.get_parts(array, return_index=True)
    rings, ring_polygon = shapely.get_rings(polygons, return_index=True)
    coords, coord_ring = shapely.get_coordinates(rings, return_index=True)
    return coords, coord_ring, ring_polygon, polygon_owner


def segment_use(coords, coord_ring):
    """Start index of every ring segment, and how many times its undirected segment occurs in all rings."""
    starts = np.nonzero(coord_ring[1:] == coord_ring[:-1])[0]
    a, b = coords[starts], coords[starts + 1]
    swap = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))
    key = np.hstack([np.where(swap[:, None], b, a), np.where(swap[:, None], a, b)])
    order = np.lexsort((key[:, 3], key[:, 2], key[:, 1], key[:, 0]))
    ordered = key[order]
    first = np.ones(len(ordered), dtype=bool)
    first[1:] = np.any(ordered[1:] != ordered[:-1], axis=1)
    group = np.cumsum(first) - 1
    uses = np.empty(len(starts), dtype=np.int64)
    uses[order] = np.bincount(group)[group]
    return starts, uses


def missing_vertices(coords, starts, uses):
    """Vertices that lie on a border segment of a neighbor that lacks them.

    Only segments used by a single ring can lack a vertex. Returns (segment
    start index, position along the segment, vertex) for each vertex to add.
    """
    single = starts[uses == 1]
    p, q = coords[single], coords[single + 1]
    ends = np.unique(np.vstack([p, q]), axis=0)
    tree = shapely.STRtree(shapely.points(ends))
    lines = shapely.linestrings(np.stack([p, q], axis=1))
    segment, end = tree.query(lines, predicate='dwithin', distance=VERTEX_MATCH_DEGREES)
    vertex = ends[end]
    start, stop = p[segment], q[segment]
    along = stop - start
    position = ((vertex - start) * along).sum(axis=1) / (along * along).sum(axis=1)
    is_end = np.all(vertex == start, axis=1) | np.all(vertex == stop, axis=1)
    keep = ~is_end & (position > 0) & (position < 1)
    return single[segment[keep]], position[keep], vertex[keep]


def insert_vertices(coords, coord_ring, segment, position, vertex):
    """The ring table with each vertex added after the start of its segment, in order along it."""
    order = np.lexsort((vertex[:, 1], vertex[:, 0], position, segment))
    at = segment[order] + 1
    return np.insert(coords, at, vertex[order], axis=0), np.insert(coord_ring, at, coord_ring[segment[order]])


def rebuild_geometries(coords, coord_ring, ring_polygon, polygon_owner):
    rings = shapely.linearrings(coords, indices=coord_ring)
    polygons = shapely.polygons(rings, indices=ring_polygon)
    return shapely.multipolygons(polygons, indices=polygon_owner)


def match_shared_vertices(geometries):
    """Gives neighbors the same vertices along their common borders.

    TBB cuts the ocean strips against the land zones and rounds the cut points
    to 6 decimals, so the strip has a vertex where the land zone has none.
    Such a vertex is added to the neighbor's segment. Returns the geometries
    and the number of vertices added.
    """
    array = np.array(geometries, dtype=object)
    added = 0
    while True:
        coords, coord_ring, ring_polygon, polygon_owner = ring_table(array)
        starts, uses = segment_use(coords, coord_ring)
        segment, position, vertex = missing_vertices(coords, starts, uses)
        if len(segment) == 0:
            return list(array), added
        added += len(segment)
        coords, coord_ring = insert_vertices(coords, coord_ring, segment, position, vertex)
        array = rebuild_geometries(coords, coord_ring, ring_polygon, polygon_owner)


# ---------------------------------------------------------------- layer 2: overlaps

def overlap_pairs(geometries):
    """Index pairs (a, b), a < b, of zones sharing more than OVERLAP_MIN_AREA_DEG2."""
    array = np.array(geometries, dtype=object)
    left, right = shapely.STRtree(array).query(array, predicate='intersects')
    pairs = []
    for a, b in sorted(zip(left.tolist(), right.tolist())):
        if a < b and polygonal_area(shapely.intersection(array[a], array[b])) > OVERLAP_MIN_AREA_DEG2:
            pairs.append((a, b))
    return pairs


def connected_groups(pairs):
    """Groups of indices linked by the pairs, each sorted, in order of their first index."""
    parent = {}

    def find(item):
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for a, b in pairs:
        parent[find(b)] = find(a)
    groups = {}
    for item in sorted(parent):
        groups.setdefault(find(item), []).append(item)
    return sorted(groups.values())


def split_group(indices, geometries):
    """Cuts a group of overlapping zones into pieces that do not overlap.

    The zone borders are noded together and made into faces, so neighboring
    pieces share exactly the same vertices. Returns [(indices of the zones
    covering the piece, geometry)], sorted by the index tuples.
    """
    borders = shapely.union_all([shapely.boundary(geometries[index]) for index in indices])
    faces = shapely.get_parts(shapely.polygonize([borders]))
    inside_points = shapely.point_on_surface(faces)
    cover = np.zeros((len(faces), len(indices)), dtype=bool)
    for column, index in enumerate(indices):
        cover[:, column] = shapely.contains(geometries[index], inside_points)
    by_cover = {}
    for row, face in enumerate(faces):
        key = tuple(index for column, index in enumerate(indices) if cover[row, column])
        if key:
            by_cover.setdefault(key, []).append(face)
    return [(key, shapely.coverage_union_all(by_cover[key])) for key in sorted(by_cover)]


def check_split(indices, geometries, pieces):
    """Each zone must equal the union of its pieces, up to rounding."""
    for index in indices:
        rebuilt = shapely.union_all([geometry for key, geometry in pieces if index in key])
        error = shapely.symmetric_difference(rebuilt, geometries[index]).area
        if error > 1e-9:
            raise BuildError(f'zone {index}: its pieces differ from it by {error} square degrees')


def timezone_pieces(geometries):
    """All zones as pieces that do not overlap: [(indices of the zones covering the piece, geometry)].

    TBB lets some zones overlap on purpose (Xinjiang, the West Bank, Abkhazia
    and others). A coverage must not overlap, so each group of overlapping
    zones is cut into pieces: the part covered by one zone only, and each
    shared part once. Zones outside every group stay whole.
    """
    groups = connected_groups(overlap_pairs(geometries))
    grouped = {index for group in groups for index in group}
    pieces = [((index,), geometry) for index, geometry in enumerate(geometries) if index not in grouped]
    for group in groups:
        split = split_group(group, geometries)
        check_split(group, geometries, split)
        pieces.extend(split)
    return sorted(pieces, key=lambda piece: piece[0]), groups


def assemble_zones(count, keys, geometries):
    """Each zone as the union of the pieces it covers; their common borders dissolve."""
    members = [[] for _ in range(count)]
    for key, geometry in zip(keys, geometries):
        for index in key:
            members[index].append(geometry)
    return [parts[0] if len(parts) == 1 else shapely.coverage_union_all(parts) for parts in members]


def odd_segments(rings):
    """Undirected segments used an odd number of times by the integer rings, as sorted rows (x1, y1, x2, y2)."""
    if not rings:
        return np.zeros((0, 4), dtype=np.int64)
    a = np.vstack(rings)
    b = np.vstack([np.roll(ring, -1, axis=0) for ring in rings])
    swap = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))
    key = np.hstack([np.where(swap[:, None], b, a), np.where(swap[:, None], a, b)])
    unique, counts = np.unique(key, axis=0, return_counts=True)
    return unique[counts % 2 == 1]


def on_world_edge(segments):
    """Whether each segment (x1, y1, x2, y2) lies on the edge of the longitude and latitude range."""
    east_west = (np.abs(segments[:, 0]) == 180 * SCALE) & (segments[:, 0] == segments[:, 2])
    north_south = (np.abs(segments[:, 1]) == 90 * SCALE) & (segments[:, 1] == segments[:, 3])
    return east_west | north_south


def geometry_rings(geometry):
    """All stored integer rings of a geometry."""
    return [ring for polygon in polygon_parts(geometry) for ring in polygon_rings(polygon)[0]]


def check_no_gaps(geometries):
    """The stored pieces must tile the whole longitude and latitude range.

    Every segment between two pieces is then used twice. A segment used once
    inside the range would be the edge of a gap or of an unwanted overlap.
    Returns the number of segments on the edge of the range.
    """
    odd = odd_segments([ring for geometry in geometries for ring in geometry_rings(geometry)])
    edge = on_world_edge(odd)
    if not np.all(edge):
        raise BuildError(f'{int((~edge).sum())} piece segments have no partner, for example {odd[~edge][:3].tolist()}')
    return int(edge.sum())


def check_zones_match_pieces(keys, pieces, zones):
    """A zone and its pieces must store the same border once their common borders cancel."""
    split_zones = sorted({index for key in keys for index in key if sum(index in other for other in keys) > 1})
    for index in split_zones:
        mine = [ring for key, piece in zip(keys, pieces) if index in key for ring in geometry_rings(piece)]
        if not np.array_equal(odd_segments(mine), odd_segments(geometry_rings(zones[index]))):
            raise BuildError(f'zone {index} differs from the union of its pieces')


def timezone_shapes():
    """Layer 2 as (IANA id, IANA id, geometry), plus facts for dataset_info."""
    ids, zones = read_tbb()
    log('TBB zones', len(ids), 'vertices', int(shapely.get_num_coordinates(zones).sum()))
    zones, added_before = match_shared_vertices(zones)
    pieces, groups = timezone_pieces(zones)
    keys = [key for key, _ in pieces]
    matched, added_after = match_shared_vertices([geometry for _, geometry in pieces])
    log('timezone pieces', len(pieces), 'overlap groups', len(groups), 'vertices added', added_before + added_after)
    simplified = simplify_coverage(matched)
    edge_segments = check_no_gaps(simplified)
    zones = assemble_zones(len(ids), keys, simplified)
    check_zones_match_pieces(keys, simplified, zones)
    shared = [key for key in keys if len(key) > 1]
    facts = {
        'layer.2.overlap_groups': str(len(groups)),
        'layer.2.pieces': str(len(pieces)),
        'layer.2.shared_pieces': json.dumps([[ids[index] for index in key] for key in shared]),
        'layer.2.shared_vertices_added': str(added_before + added_after),
        'layer.2.unpaired_segments_inside_the_range': '0',
        'layer.2.segments_on_the_edge_of_the_range': str(edge_segments),
    }
    return [(zone_id, zone_id, geometry) for zone_id, geometry in zip(ids, zones)], facts


def reviewed_timezone_shapes():
    """The reviewed timezone areas as layer 2 shapes (zone, "reviewed:" + area id, geometry), and their list.

    Each adds its zone as one more candidate where it lies. Nothing is taken
    away from the boundary data, so the coverage proved above still holds.
    """
    shapes, listed = [], []
    for feature in sorted(read_geojson('reviewed_timezone_areas'), key=lambda item: item['properties']['id']):
        area_id, zone = feature['properties']['id'], feature['properties']['timezone']
        if zone.startswith('Etc/') or '/' not in zone:
            raise BuildError(f'reviewed timezone area {area_id}: {zone} is not a civil zone')
        geometry = shape(feature['geometry'])
        if not geometry.is_valid or geometry.is_empty:
            raise BuildError(f'reviewed timezone area {area_id}: its outline is not a valid polygon')
        shapes.append((zone, f'reviewed:{area_id}', geometry))
        listed.append([area_id, zone])
    return shapes, listed


# ---------------------------------------------------------------- layer 3

def reviewed_shapes():
    """Layer 3 as (area id, area id, geometry), and the reviewed_areas table rows."""
    features = {feature['properties']['id']: feature for feature in read_geojson('reviewed_areas')}
    shapes, table = [], []
    for area_id in sorted(REVIEWED_AREAS):
        if area_id not in features:
            raise BuildError(f'reviewed area {area_id} is not in the draft areas file')
        rule, region_id, reference, name = REVIEWED_AREAS[area_id]
        feature = features[area_id]
        shapes.append((area_id, area_id, shape(feature['geometry'])))
        table.append((area_id, name or feature['properties']['name_en'], rule, region_id, reference))
    return shapes, table


# ---------------------------------------------------------------- encoding

def quantize(values):
    """Degrees to integer units of 1e-5 degree: floor(v * 100000 + 0.5)."""
    return np.floor(np.asarray(values, dtype=np.float64) * SCALE + 0.5).astype(np.int64)


def quantize_ring(coordinates):
    """A ring as an (n, 2) integer array of (lon, lat), open, without repeated neighbors."""
    points = quantize(coordinates)
    keep = np.ones(len(points), dtype=bool)
    keep[1:] = np.any(points[1:] != points[:-1], axis=1)
    points = points[keep]
    if len(points) > 1 and np.array_equal(points[0], points[-1]):
        points = points[:-1]
    return points


def doubled_area(points):
    """Twice the signed area of an integer ring, exact (positive when counterclockwise)."""
    x, y = points[:, 0], points[:, 1]
    cross = x * np.roll(y, -1) - np.roll(x, -1) * y
    return sum(cross.tolist())


def canonical_ring(points, counterclockwise):
    """The same ring in one fixed form: the wanted direction, starting at its smallest (lon, lat) point."""
    area = doubled_area(points)
    if area != 0 and (area > 0) != counterclockwise:
        points = points[::-1]
    start = np.lexsort((points[:, 1], points[:, 0]))[0]
    return np.roll(points, -start, axis=0)


def polygon_rings(polygon):
    """The stored rings of a polygon and the number of rings dropped.

    A ring with fewer than 3 points after rounding is dropped. A polygon whose
    outer ring is dropped is dropped whole: ([], all its rings). The outer ring
    comes first and runs counterclockwise; holes run clockwise, sorted.
    """
    outer = quantize_ring(shapely.get_coordinates(polygon.exterior))
    if len(outer) < 3:
        return [], 1 + len(polygon.interiors)
    holes = [quantize_ring(shapely.get_coordinates(ring)) for ring in polygon.interiors]
    kept = [canonical_ring(hole, False) for hole in holes if len(hole) >= 3]
    kept.sort(key=lambda hole: hole.tobytes())
    return [canonical_ring(outer, True)] + kept, len(holes) - len(kept)


def varint(value):
    """Unsigned LEB128."""
    out = bytearray()
    while True:
        low = value & 0x7F
        value >>= 7
        if value:
            out.append(low | 0x80)
        else:
            out.append(low)
            return bytes(out)


def zigzag(value):
    return (value << 1) ^ (value >> 63)


def encode_ring(points):
    """Point count, then zigzag varints: the first point absolute, the rest as differences."""
    deltas = np.diff(points, axis=0, prepend=np.zeros((1, 2), dtype=np.int64))
    out = bytearray(varint(len(points)))
    for value in deltas.ravel().tolist():
        out += varint(zigzag(value))
    return bytes(out)


def encode_rings(rings):
    return varint(len(rings)) + b''.join(encode_ring(points) for points in rings)


def polygon_row(layer, code, source_code, polygon, drops):
    """One areas row without its id, plus its vertex count; None when the polygon is dropped."""
    rings, dropped = polygon_rings(polygon)
    drops[layer, 'rings'] += dropped
    if not rings:
        drops[layer, 'polygons'] += 1
        return None
    points = np.vstack(rings)
    return (layer, code, source_code, int(points[:, 1].min()), int(points[:, 0].min()),
            int(points[:, 1].max()), int(points[:, 0].max()), encode_rings(rings), len(points))


def layer_rows(layer, shapes, drops):
    rows = []
    for code, source_code, geometry in shapes:
        for polygon in polygon_parts(geometry):
            row = polygon_row(layer, code, source_code, polygon, drops)
            if row is not None:
                rows.append(row)
    return rows


def row_order(row):
    """Sort key: layer, code with NULL last, source code, bounding box, blob bytes."""
    layer, code, source_code, min_lat, min_lon, max_lat, max_lon, blob, _ = row
    return (layer, code is None, (code or '').encode('utf-8'), source_code.encode('utf-8'),
            min_lat, min_lon, max_lat, max_lon, blob)


# ---------------------------------------------------------------- output

def dataset_info(rows, drops, extra):
    info = {
        'format_version': str(FORMAT_VERSION),
        'coordinate_units_per_degree': str(SCALE),
        'simplification_tolerance_degrees': str(TOLERANCE_DEGREES),
        'simplification_method': 'shapely.coverage_simplify on layers 0 and 2; layers 1 and 3 and the reviewed '
                                 'timezone areas of layer 2 as published',
        'timezone_overlap_min_area_square_degrees': str(OVERLAP_MIN_AREA_DEG2),
        'row_order': 'layer, code (NULL last), source_code, min_lat, min_lon, max_lat, max_lon, rings',
        'builder.python': '.'.join(str(part) for part in sys.version_info[:3]),
        'builder.shapely': shapely.__version__,
        'builder.geos': shapely.geos_version_string,
        'builder.numpy': np.__version__,
        'builder.pyshp': shapefile.__version__,
    }
    for key in sorted(SOURCES):
        for field in ('name', 'version', 'sha256', 'license', 'path'):
            info[f'source.{key}.{field}'] = SOURCES[key][field]
    for layer in LAYERS:
        mine = [row for row in rows if row[0] == layer]
        info[f'layer.{layer}.rows'] = str(len(mine))
        info[f'layer.{layer}.vertices'] = str(sum(row[8] for row in mine))
        info[f'layer.{layer}.dropped_rings'] = str(drops[layer, 'rings'])
        info[f'layer.{layer}.dropped_polygons'] = str(drops[layer, 'polygons'])
    info.update(extra)
    return info


def write_database(path, rows, reviewed, info):
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute('PRAGMA page_size = 4096')
    db.execute('BEGIN')
    for statement in SCHEMA:
        db.execute(statement)
    db.executemany('INSERT INTO areas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   [(area_id, *row[:8]) for area_id, row in enumerate(rows, start=1)])
    db.executemany('INSERT INTO reviewed_areas VALUES (?, ?, ?, ?, ?)', reviewed)
    db.executemany('INSERT INTO dataset_info VALUES (?, ?)', sorted(info.items()))
    db.execute(INDEX)
    db.execute(f'PRAGMA user_version = {FORMAT_VERSION}')
    db.execute('COMMIT')
    db.execute('VACUUM')
    db.close()


def build_rows():
    """All areas rows in file order, the reviewed_areas rows and the dataset_info entries."""
    iso2_to_iso3 = read_country_info()
    iso3_to_iso2 = {iso3: iso2 for iso2, iso3 in iso2_to_iso3.items()}
    drops = Counter()
    extra = {}

    countries = country_shapes(iso3_to_iso2)
    rows = layer_rows(LAYER_COUNTRY, countries, drops)
    log('layer 0 rows', len(rows))

    territories, missing = territory_shapes(territory_codes(iso2_to_iso3, countries))
    extra['layer.1.codes'] = ' '.join(code for code, _, _ in territories)
    extra['layer.1.codes_without_natural_earth_polygon'] = ' '.join(missing)
    extra['layer.1.codes_left_out'] = ' '.join(NOT_IN_OVERLAY)
    rows += layer_rows(LAYER_TERRITORY, territories, drops)
    log('layer 1 codes', len(territories), 'without a Natural Earth polygon', missing)
    del countries

    zones, facts = timezone_shapes()
    extra.update(facts)
    known_zones = {code for code, _, _ in zones}
    rows += layer_rows(LAYER_TIMEZONE, zones, drops)
    added, listed = reviewed_timezone_shapes()
    unknown = sorted({zone for zone, _, _ in added} - known_zones)
    if unknown:
        raise BuildError(f'reviewed timezone areas name zones the boundary data does not have: {unknown}')
    rows += layer_rows(LAYER_TIMEZONE, added, drops)
    extra['layer.2.reviewed_areas'] = json.dumps(listed)
    log('layer 2 done, with', len(added), 'reviewed timezone areas')

    reviewed, table = reviewed_shapes()
    rows += layer_rows(LAYER_REVIEWED, reviewed, drops)

    rows.sort(key=row_order)
    return rows, table, dataset_info(rows, drops, extra)


def build(path):
    verify_sources()
    rows, table, info = build_rows()
    write_database(path, rows, table, info)
    log('wrote', path, os.path.getsize(path), 'bytes, SHA-256', sha256_of(path))
    for key in sorted(info):
        if key.startswith('layer.') and 'shared_pieces' not in key:
            log('  ', key, '=', info[key])


def main():
    path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        build(path)
    except BuildError as error:
        sys.exit(f'BUILD FAILED: {error}')


if __name__ == '__main__':
    main()
