"""Tests of the steps inside build_city_boundaries.py.

Run from the repository root, with the Python environment of requirements.txt:

    python -m unittest discover -s tool/geo -p "test_*.py"

They cover the area measure, the choice of an area for each city, the label
of an area several cities name, the simplification guard, the file order and
the count of identified cities. The name and acceptance rules are covered by
test_city_area_rules.py, and the built file itself by
test/dataset/city_boundary_dataset_test.dart.
"""
import os
import sys
import unittest

import numpy as np
from shapely.geometry import MultiPolygon, Polygon, box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_city_boundaries as build  # noqa: E402
import city_area_rules as rules  # noqa: E402
from city_area_rules import Candidate, City  # noqa: E402


class AreaTest(unittest.TestCase):
    def test_a_square_degree_at_the_equator(self):
        # 111.32 km of longitude by 110.57 km of latitude.
        self.assertAlmostEqual(build.area_km2([box(0, 0, 1, 1)])[0], 12308, delta=40)

    def test_a_square_degree_is_about_half_as_large_at_latitude_sixty(self):
        self.assertAlmostEqual(build.area_km2([box(10, 59.5, 11, 60.5)])[0], 6213, delta=40)

    def test_the_parts_of_a_multipolygon_add_up(self):
        two = MultiPolygon([box(0, 0, 1, 1), box(2, 0, 3, 1)])
        self.assertAlmostEqual(build.area_km2([two])[0], 2 * build.area_km2([box(0, 0, 1, 1)])[0], places=6)


class OtherPlacesTest(unittest.TestCase):
    def setUp(self):
        self.jeddah = build.Place(rules.place_names(['Jeddah', 'جدة']), 'city', 3430697)
        self.thuwal = build.Place(rules.place_names(['Thuwal']), 'town', 17792)
        self.dhahran = build.Place(rules.place_names(['Dhahran']), 'city', 120521)
        self.governorate = rules.area_names(['Governorate of Jidda', 'محافظة جدة'])

    def test_the_place_that_carries_the_name_of_the_area_is_not_another_place(self):
        self.assertEqual(build.other_places([self.jeddah], self.governorate, 'county', 'TR'), (0, 0))

    def test_a_town_inside_counts_against_the_area(self):
        self.assertEqual(build.other_places([self.jeddah, self.thuwal], self.governorate, 'county', 'TR'), (1, 0))

    def test_a_small_town_inside_a_saudi_governorate_is_tolerated_and_counted_apart(self):
        self.assertEqual(build.other_places([self.jeddah, self.thuwal], self.governorate, 'county', 'SA'), (0, 1))

    def test_another_city_inside_a_saudi_governorate_counts_against_it(self):
        self.assertEqual(build.other_places([self.thuwal, self.dhahran], self.governorate, 'county', 'SA'), (1, 1))


class ChooseAreasTest(unittest.TestCase):
    def test_the_smallest_area_of_the_name_is_taken_and_accepted_when_it_is_the_place_itself(self):
        city = Candidate('city', 'locality', 479.0, 1)
        emirate = Candidate('emirate', 'region', 2549.0, 19)
        self.assertEqual(build.choose_areas({7: [emirate, city]}), {7: (city, build.OUTCOME_IDENTIFIED)})

    def test_a_unit_that_holds_another_city_or_town_gives_the_city_no_boundary(self):
        governorate = Candidate('dammam', 'county', 1847.0, 2)
        self.assertEqual(build.choose_areas({7: [governorate]}), {7: (governorate, build.OUTCOME_OTHER_PLACES)})

    def test_a_unit_that_holds_only_its_city_is_accepted(self):
        governorate = Candidate('khobar', 'county', 198.0, 0)
        self.assertEqual(build.choose_areas({7: [governorate]}), {7: (governorate, build.OUTCOME_IDENTIFIED)})

    def test_a_larger_area_of_the_name_never_replaces_a_refused_smaller_one(self):
        # The region holds everything the refused governorate holds.
        governorate = Candidate('governorate', 'county', 3333.0, 1)
        region = Candidate('region', 'region', 150000.0, 0)
        self.assertEqual(build.choose_areas({7: [region, governorate]}),
                         {7: (governorate, build.OUTCOME_OTHER_PLACES)})

    def test_a_city_without_an_area_of_its_name_has_no_boundary(self):
        self.assertEqual(build.choose_areas({7: []}), {7: (None, build.OUTCOME_NO_AREA)})


class LabelAreasTest(unittest.TestCase):
    def setUp(self):
        self.cities = [City(10, 298343, 'New Cairo City'), City(11, 300000, 'New Cairo'), City(12, 5000, 'Other')]
        self.area = Candidate('new-cairo', 'locality', 300.0, 0)
        self.other = Candidate('other', 'locality', 20.0, 0)

    def test_an_area_two_cities_name_is_labeled_with_the_most_populous(self):
        chosen = {0: (self.area, build.OUTCOME_IDENTIFIED), 1: (self.area, build.OUTCOME_IDENTIFIED),
                  2: (self.other, build.OUTCOME_IDENTIFIED)}
        owners, outcomes = build.label_areas(chosen, self.cities)
        self.assertEqual(owners, {'new-cairo': 1, 'other': 2})
        self.assertEqual(outcomes, {0: build.OUTCOME_NAMED_BY_LARGER_CITY, 1: build.OUTCOME_IDENTIFIED,
                                    2: build.OUTCOME_IDENTIFIED})

    def test_a_refused_area_is_labeled_by_nobody(self):
        chosen = {0: (self.area, build.OUTCOME_OTHER_PLACES), 2: (None, build.OUTCOME_NO_AREA)}
        owners, outcomes = build.label_areas(chosen, self.cities)
        self.assertEqual(owners, {})
        self.assertEqual(outcomes, {0: build.OUTCOME_OTHER_PLACES, 2: build.OUTCOME_NO_AREA})


def _area(source_id, level, place_class, geometry):
    return build.Area(source_id, level, 'DE', source_id, frozenset(), geometry,
                      float(build.area_km2([geometry])[0]), 0, 0, place_class)


class PartsOfLargerCitiesTest(unittest.TestCase):
    """An accepted area inside a larger accepted area is left out when it is a part of that city."""

    def setUp(self):
        self.city = _area('berlin', 'region', None, box(13.0, 52.3, 13.8, 52.7))
        # Position 0 is the city's own, inside its central district; position 1 is the district's.
        self.positions = np.array([(13.41, 52.52), (13.40, 52.53), (13.2, 52.4), (20.0, 40.0)])

    def parts(self, *inner):
        areas = {area.source_id: area for area in (self.city, *inner)}
        owners = {'berlin': 0, **{area.source_id: index for index, area in enumerate(inner, start=1)}}
        return build.parts_of_larger_cities(areas, owners, self.positions)

    def test_a_district_with_an_outline_of_its_own_is_a_part_of_its_city(self):
        district = _area('mitte', 'locality', None, box(13.35, 52.50, 13.45, 52.55))
        self.assertEqual(self.parts(district), {'mitte'})

    def test_a_town_of_the_source_inside_a_larger_area_keeps_its_boundary(self):
        town = _area('town', 'locality', 'town', box(13.35, 52.50, 13.45, 52.55))
        self.assertEqual(self.parts(town), set())

    def test_the_larger_area_is_never_the_part(self):
        district = _area('mitte', 'locality', None, box(13.35, 52.50, 13.45, 52.55))
        self.assertNotIn('berlin', self.parts(district))

    def test_an_area_inside_no_other_area_stays_whatever_the_source_calls_it(self):
        elsewhere = _area('elsewhere', 'locality', None, box(19.9, 39.9, 20.1, 40.1))
        areas = {'berlin': self.city, 'elsewhere': elsewhere}
        self.assertEqual(build.parts_of_larger_cities(areas, {'berlin': 0, 'elsewhere': 3}, self.positions), set())

    def test_a_part_loses_its_label_and_its_city_is_recorded_as_a_part(self):
        owners, outcomes = build.without_parts(
            {'berlin': 0, 'mitte': 1},
            {0: build.OUTCOME_IDENTIFIED, 1: build.OUTCOME_IDENTIFIED, 2: build.OUTCOME_NO_AREA},
            {'mitte'})
        self.assertEqual(owners, {'berlin': 0})
        self.assertEqual(outcomes, {0: build.OUTCOME_IDENTIFIED, 1: build.OUTCOME_PART_OF_LARGER_CITY,
                                    2: build.OUTCOME_NO_AREA})

    def test_a_district_inside_a_district_inside_a_city_are_both_parts(self):
        district = _area('district', 'county', None, box(13.3, 52.45, 13.5, 52.6))
        quarter = _area('quarter', 'locality', None, box(13.35, 52.50, 13.45, 52.55))
        self.assertEqual(self.parts(district, quarter), {'district', 'quarter'})


class AreaKindRowsTest(unittest.TestCase):
    def test_one_row_by_city_for_each_kept_area_that_is_named_as_a_unit(self):
        areas = {
            'jeddah': _area('jeddah', 'county', None, box(39.0, 21.2, 39.4, 21.9))._replace(country='SA'),
            'khobar': _area('khobar', 'county', None, box(50.1, 26.2, 50.3, 26.3))._replace(country='SA'),
            'unaizah': _area('unaizah', 'locality', 'city', box(43.9, 26.0, 44.0, 26.1))._replace(country='SA'),
            'safranbolu': _area('safranbolu', 'county', None, box(32.6, 41.2, 32.8, 41.3))._replace(country='TR'),
        }
        kept = [build.KeptArea('khobar', 109323, 198.0, areas['khobar'].geometry),
                build.KeptArea('safranbolu', 740430, 770.0, areas['safranbolu'].geometry),
                build.KeptArea('jeddah', 105343, 3333.0, areas['jeddah'].geometry),
                build.KeptArea('unaizah', 107312, 291.0, areas['unaizah'].geometry)]
        self.assertEqual(build.area_kind_rows(kept, areas), [(105343, 'governorate'), (109323, 'governorate')])

    def test_no_row_when_no_kept_area_is_named_as_a_unit(self):
        area = _area('paris', 'locality', 'city', box(2.2, 48.8, 2.5, 48.9))._replace(country='FR')
        self.assertEqual(build.area_kind_rows([build.KeptArea('paris', 2988507, 105.0, area.geometry)],
                                              {'paris': area}), [])


class SimplifyTest(unittest.TestCase):
    def test_a_vertex_that_bends_the_outline_by_less_than_the_tolerance_is_removed(self):
        bend = build.TOLERANCE_DEGREES / 10
        outline = Polygon([(0, 0), (0.5, bend), (1, 0), (1, 1), (0, 1)])
        simplified, kept_published = build.simplified(outline, 0.5, 0.5)
        self.assertFalse(kept_published)
        self.assertEqual(len(simplified.exterior.coords), 5)

    def test_a_vertex_that_bends_it_by_more_stays(self):
        bend = build.TOLERANCE_DEGREES * 10
        outline = Polygon([(0, 0), (0.5, bend), (1, 0), (1, 1), (0, 1)])
        simplified, _ = build.simplified(outline, 0.5, 0.5)
        self.assertEqual(len(simplified.exterior.coords), 6)

    def test_the_published_outline_is_kept_when_simplifying_would_put_the_city_outside(self):
        bend = build.TOLERANCE_DEGREES / 2
        # The city stands on the small bulge that simplification cuts off.
        outline = Polygon([(0, 0), (0.5, -bend), (1, 0), (1, 1), (0, 1)])
        simplified, kept_published = build.simplified(outline, 0.5, -bend / 2)
        self.assertTrue(kept_published)
        self.assertTrue(simplified.equals(outline))


class FileOrderTest(unittest.TestCase):
    def test_the_rows_of_a_smaller_area_come_first_so_the_first_row_that_holds_a_point_is_the_smallest_unit(self):
        governorate = build.KeptArea('b', 2, 4000.0, box(39, 21, 40, 22))
        town = build.KeptArea('a', 1, 7.0, box(39.4, 21.4, 39.5, 21.5))
        rows = build.area_rows([governorate, town], drops={'rings': 0, 'polygons': 0})
        self.assertEqual([row[0] for row in rows], [1, 2])
        self.assertEqual([row[1] for row in rows], ['a', 'b'])

    def test_equal_areas_are_ordered_by_source_id(self):
        first = build.KeptArea('b', 2, 10.0, box(0, 0, 1, 1))
        second = build.KeptArea('a', 1, 10.0, box(5, 5, 6, 6))
        rows = build.area_rows([first, second], drops={'rings': 0, 'polygons': 0})
        self.assertEqual([row[1] for row in rows], ['a', 'b'])

    def test_each_part_of_an_area_is_a_row_with_its_bounds_in_units(self):
        islands = build.KeptArea('a', 1, 10.0, MultiPolygon([box(0, 0, 1, 1), box(2, 0, 3, 1)]))
        rows = build.area_rows([islands], drops={'rings': 0, 'polygons': 0})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][2:6], (0, 0, 100000, 100000))
        self.assertEqual(rows[1][2:6], (0, 200000, 100000, 300000))


class IdentificationTest(unittest.TestCase):
    def test_a_city_is_identified_only_when_the_smallest_area_that_holds_it_is_its_own(self):
        cities = [City(1, 4000000, 'Large'), City(2, 60000, 'Town inside'), City(3, 200000, 'District row'),
                  City(4, 120000, 'Elsewhere')]
        positions = np.array([(39.2, 21.5), (39.45, 21.45), (39.8, 21.8), (50.0, 30.0)])
        kept = [build.KeptArea('town', 2, 7.0, box(39.4, 21.4, 39.5, 21.5)),
                build.KeptArea('large', 1, 4000.0, box(39, 21, 40, 22))]
        facts = build.identification(cities, positions, kept, populations=(0, 100000))
        self.assertEqual(facts, {
            'identification.population_0.cities': '4',
            'identification.population_0.inside': '3',
            'identification.population_0.identified': '2',
            'identification.population_100000.cities': '3',
            'identification.population_100000.inside': '2',
            'identification.population_100000.identified': '1',
        })


class CombinedChecksumTest(unittest.TestCase):
    def test_it_depends_on_every_file_and_not_on_their_order(self):
        first = {'key': 'a', 'sha256': '11'}
        second = {'key': 'b', 'sha256': '22'}
        self.assertEqual(build.combined_sha256([first, second]), build.combined_sha256([second, first]))
        self.assertNotEqual(build.combined_sha256([first, second]),
                            build.combined_sha256([first, {'key': 'b', 'sha256': '23'}]))
        self.assertNotEqual(build.combined_sha256([first, second]), build.combined_sha256([first]))


if __name__ == '__main__':
    unittest.main()
