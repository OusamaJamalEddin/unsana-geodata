"""Reference reader of geo.db. Standard library only.

    python reference_lookup.py geo.db 21.4225,39.8262 31.77805,35.23542
    python reference_lookup.py geo.db --csv points.csv        (columns: id,lat,lon)

Prints one CSV line per point: id,lat,lon,layer0,layer1,layer2,layer3. Each
layer column is the sorted list of distinct codes of the rows that contain the
point, joined with "|". A NULL code prints as "~". The column is empty when no
row of that layer contains the point.

Lookup rules, which a reader in another language must mirror exactly:
  1. Quantize: x = floor(lon * 100000 + 0.5), y = floor(lat * 100000 + 0.5).
  2. Candidate rows: min_lat <= y <= max_lat and min_lon <= x <= max_lon.
  3. A row contains the point when the number of crossed edges, counted over
     ALL its rings, is odd. An edge (x1, y1)-(x2, y2) is crossed when
     (y1 > y) != (y2 > y) and
         (x - x1) * (y2 - y1) < (x2 - x1) * (y - y1)   if y2 > y1
         (x - x1) * (y2 - y1) > (x2 - x1) * (y - y1)   otherwise.
     Integer arithmetic only. Each ring is closed by an edge from its last
     point to its first point.

Decoded rows are kept in memory, so a long run uses up to about 300 MB.
"""
import csv
import math
import sqlite3
import sys

SCALE = 100000
LAYER_COUNT = 4
NULL_CODE = '~'
# No ORDER BY here: with it SQLite 3.45 scans the table instead of using the areas_by_box index.
CANDIDATES = ('SELECT area_id, layer, code FROM areas '
              'WHERE min_lat <= ? AND max_lat >= ? AND min_lon <= ? AND max_lon >= ?')
RINGS = 'SELECT rings FROM areas WHERE area_id = ?'


def quantize(degrees):
    """Degrees to integer units of 1e-5 degree."""
    return math.floor(degrees * SCALE + 0.5)


def read_varint(blob, position):
    """Unsigned LEB128 at position: (value, next position)."""
    value = 0
    shift = 0
    while True:
        byte = blob[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, position
        shift += 7


def unzigzag(value):
    return (value >> 1) ^ -(value & 1)


def decode_rings(blob):
    """The rings of a blob as a list of (xs, ys) integer lists. Ring 0 is the outer ring."""
    ring_count, position = read_varint(blob, 0)
    rings = []
    for _ in range(ring_count):
        point_count, position = read_varint(blob, position)
        xs, ys = [], []
        x = y = 0
        for _ in range(point_count):
            dx, position = read_varint(blob, position)
            dy, position = read_varint(blob, position)
            x += unzigzag(dx)
            y += unzigzag(dy)
            xs.append(x)
            ys.append(y)
        rings.append((xs, ys))
    if position != len(blob):
        raise ValueError('trailing bytes in a rings blob')
    return rings


def ring_crossings(xs, ys, x, y):
    """How many edges of one ring the point crosses."""
    count = 0
    x1 = xs[-1]
    y1 = ys[-1]
    for x2, y2 in zip(xs, ys):
        if (y1 > y) != (y2 > y):
            left = (x - x1) * (y2 - y1)
            right = (x2 - x1) * (y - y1)
            if (left < right) if y2 > y1 else (left > right):
                count += 1
        x1 = x2
        y1 = y2
    return count


def rings_contain(rings, x, y):
    """Even-odd rule over all rings of a row."""
    return sum(ring_crossings(xs, ys, x, y) for xs, ys in rings) % 2 == 1


class GeoDb:
    """Point lookups in a geo.db file."""

    def __init__(self, path):
        self._db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        version = self._db.execute('PRAGMA user_version').fetchone()[0]
        if version != 1:
            raise ValueError(f'unsupported geo.db format version {version}')
        self._decoded = {}

    def _rings(self, area_id):
        if area_id not in self._decoded:
            self._decoded[area_id] = decode_rings(self._db.execute(RINGS, (area_id,)).fetchone()[0])
        return self._decoded[area_id]

    def rows_at(self, latitude, longitude):
        """(area_id, layer, code) of every row containing the point, in area_id order."""
        x = quantize(longitude)
        y = quantize(latitude)
        candidates = sorted(self._db.execute(CANDIDATES, (y, y, x, x)).fetchall())
        return [(area_id, layer, code) for area_id, layer, code in candidates
                if rings_contain(self._rings(area_id), x, y)]

    def codes_at(self, latitude, longitude):
        """Per layer, the sorted distinct codes of the rows containing the point; NULL as "~"."""
        codes = [set() for _ in range(LAYER_COUNT)]
        for _, layer, code in self.rows_at(latitude, longitude):
            codes[layer].add(NULL_CODE if code is None else code)
        return [sorted(layer_codes) for layer_codes in codes]

    def line(self, point_id, latitude, longitude):
        """One CSV row: id, lat, lon and the four layer columns."""
        return [point_id, repr(float(latitude)), repr(float(longitude))] + \
               ['|'.join(layer_codes) for layer_codes in self.codes_at(latitude, longitude)]


def points_from_arguments(arguments):
    """(id, lat, lon) for each "lat,lon" argument, or for each row of the file after --csv."""
    if arguments and arguments[0] == '--csv':
        with open(arguments[1], encoding='utf-8', newline='') as stream:
            return [(row['id'], float(row['lat']), float(row['lon'])) for row in csv.DictReader(stream)]
    points = []
    for number, argument in enumerate(arguments, start=1):
        latitude, longitude = argument.split(',')
        points.append((f'point:{number}', float(latitude), float(longitude)))
    return points


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    geo = GeoDb(sys.argv[1])
    writer = csv.writer(sys.stdout, lineterminator='\n')
    writer.writerow(['id', 'lat', 'lon', 'layer0', 'layer1', 'layer2', 'layer3'])
    for point_id, latitude, longitude in points_from_arguments(sys.argv[2:]):
        writer.writerow(geo.line(point_id, latitude, longitude))


if __name__ == '__main__':
    main()
