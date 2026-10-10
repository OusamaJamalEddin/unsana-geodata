"""Tests of city_area_rules.py: which administrative area of the source may
stand for a bundled city.

    python -m unittest discover -s tool/geo -p "test_*.py"
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import city_area_rules as rules  # noqa: E402
from city_area_rules import Candidate, City  # noqa: E402


class NormalizedNameTest(unittest.TestCase):
    def test_case_marks_spaces_and_punctuation_do_not_matter(self):
        self.assertEqual(rules.normalized_name('Al-Manṣūrah'), 'almansurah')
        self.assertEqual(rules.normalized_name("Sana'a"), 'sanaa')
        self.assertEqual(rules.normalized_name('  New York  City '), 'newyorkcity')

    def test_letters_that_do_not_decompose_are_written_out(self):
        self.assertEqual(rules.normalized_name('İstanbul'), 'istanbul')
        self.assertEqual(rules.normalized_name('Iğdır'), 'igdir')
        self.assertEqual(rules.normalized_name('Gießen'), 'giessen')
        self.assertEqual(rules.normalized_name('Łódź'), 'lodz')

    def test_arabic_marks_and_letter_forms_do_not_matter(self):
        plain = rules.normalized_name('مكة')
        self.assertEqual(rules.normalized_name('مَكَّة'), plain)
        self.assertEqual(rules.normalized_name('مكه'), plain)
        self.assertEqual(rules.normalized_name('مكـــة'), plain)
        self.assertEqual(rules.normalized_name('مستشفى'), rules.normalized_name('مستشفي'))

    def test_different_spellings_stay_different(self):
        self.assertNotEqual(rules.normalized_name('Mecca'), rules.normalized_name('Makkah'))
        self.assertNotEqual(rules.normalized_name('Jiddah'), rules.normalized_name('Jeddah'))

    def test_a_missing_name_is_empty(self):
        self.assertEqual(rules.normalized_name(None), '')
        self.assertEqual(rules.normalized_name(' - '), '')


class GenericWordsTest(unittest.TestCase):
    def test_the_word_for_the_kind_of_unit_is_removed(self):
        self.assertEqual(rules.name_without_generic_words('Dammam Governorate'), 'dammam')
        self.assertEqual(rules.name_without_generic_words('Rawalpindi City Tehsil'), 'rawalpindi')
        self.assertEqual(rules.name_without_generic_words('Kota Bekasi'), 'bekasi')
        self.assertEqual(rules.name_without_generic_words('Ajman Emirate'), 'ajman')

    def test_of_and_the_go_with_it(self):
        self.assertEqual(rules.name_without_generic_words('Governorate of Jidda'), 'jidda')
        self.assertEqual(rules.name_without_generic_words('City of the Dalles'), 'dalles')

    def test_arabic_words_for_the_kind_of_unit_are_removed(self):
        self.assertEqual(rules.name_without_generic_words('محافظة جدة'), rules.normalized_name('جدة'))
        self.assertEqual(rules.name_without_generic_words('مدينة الشارقة'), rules.normalized_name('الشارقة'))
        self.assertEqual(rules.name_without_generic_words('إمارة عجمان'), rules.normalized_name('عجمان'))

    def test_a_name_without_such_a_word_has_no_second_form(self):
        self.assertIsNone(rules.name_without_generic_words('Makkah Al Mukarramah'))
        self.assertIsNone(rules.name_without_generic_words(None))

    def test_only_a_whole_word_counts(self):
        self.assertIsNone(rules.name_without_generic_words('Cityville'))
        self.assertIsNone(rules.name_without_generic_words('Kotabaru'))

    def test_a_name_of_generic_words_alone_has_no_second_form(self):
        self.assertIsNone(rules.name_without_generic_words('City'))
        self.assertIsNone(rules.name_without_generic_words('The District of the City'))

    def test_a_county_is_not_such_a_word(self):
        # "Jackson County" must not become the city of Jackson.
        self.assertIsNone(rules.name_without_generic_words('Jackson County'))


class SameNameTest(unittest.TestCase):
    def test_an_area_is_known_by_each_name_and_by_each_name_without_its_generic_word(self):
        names = rules.area_names(['Dammam Governorate', None, 'محافظة الدمام'])
        self.assertIn('dammamgovernorate', names)
        self.assertIn('dammam', names)
        self.assertIn(rules.normalized_name('الدمام'), names)

    def test_a_city_matches_an_area_that_carries_one_of_its_names(self):
        area = rules.area_names(['Governorate of Jidda', 'محافظة جدة'])
        self.assertTrue(rules.same_name(area, rules.place_names(['Jeddah', 'جدة'])))
        self.assertFalse(rules.same_name(area, rules.place_names(['Jeddah'])))

    def test_a_city_name_is_never_shortened(self):
        # The city "Salt Lake City" is not the area "Salt Lake".
        self.assertFalse(rules.same_name(rules.area_names(['Salt Lake']), rules.place_names(['Salt Lake City'])))
        self.assertTrue(rules.same_name(rules.area_names(['Salt Lake City']), rules.place_names(['Salt Lake City'])))

    def test_empty_names_match_nothing(self):
        self.assertFalse(rules.same_name(rules.area_names([None, '']), rules.place_names([None, ' - '])))


class AcceptanceTest(unittest.TestCase):
    def test_the_outline_of_the_place_itself_is_accepted_whatever_lies_inside(self):
        self.assertTrue(rules.is_accepted('locality', other_places=3))

    def test_a_unit_above_it_is_accepted_when_it_holds_no_other_city_or_town(self):
        for level in ('localadmin', 'county', 'region'):
            self.assertTrue(rules.is_accepted(level, other_places=0))

    def test_a_unit_that_holds_another_city_or_town_is_never_the_city(self):
        for level in ('localadmin', 'county', 'region'):
            self.assertFalse(rules.is_accepted(level, other_places=1))

    def test_a_part_of_a_place_and_a_country_are_never_a_city(self):
        for level in ('neighborhood', 'macrohood', 'microhood', 'country', 'dependency'):
            self.assertFalse(rules.is_accepted(level, other_places=0))



class OtherPlacesTest(unittest.TestCase):
    def test_another_city_or_town_inside_a_unit_counts_against_it(self):
        for level in ('localadmin', 'county', 'region'):
            self.assertTrue(rules.counts_against(level, 'TR', 'city', 900000))
            self.assertTrue(rules.counts_against(level, 'TR', 'town', 5000))
            self.assertTrue(rules.counts_against(level, 'TR', 'town', None))

    def test_a_village_never_does(self):
        for kind in ('village', 'hamlet', None):
            self.assertFalse(rules.counts_against('county', 'TR', kind, 900))

    def test_a_small_town_inside_a_saudi_governorate_does_not(self):
        # Thuwal in the governorate of Jeddah, Al Hair in the one of Riyadh.
        self.assertFalse(rules.counts_against('county', 'SA', 'town', 17792))
        self.assertFalse(rules.counts_against('county', 'SA', 'town', 49999))

    def test_neither_does_a_saudi_town_of_unknown_size(self):
        self.assertFalse(rules.counts_against('county', 'SA', 'town', None))

    def test_a_large_town_inside_a_saudi_governorate_still_does(self):
        # Al Hawiyah in the governorate of Taif.
        self.assertTrue(rules.counts_against('county', 'SA', 'town', 148151))
        self.assertTrue(rules.counts_against('county', 'SA', 'town', 50000))

    def test_another_city_inside_a_saudi_governorate_always_does(self):
        # Dhahran in the governorate of Dammam.
        self.assertTrue(rules.counts_against('county', 'SA', 'city', 120521))
        self.assertTrue(rules.counts_against('county', 'SA', 'city', None))

    def test_the_saudi_rule_is_for_governorates_only(self):
        self.assertTrue(rules.counts_against('region', 'SA', 'town', 5000))
        self.assertTrue(rules.counts_against('localadmin', 'SA', 'town', 5000))

    def test_no_other_country_has_such_a_rule(self):
        self.assertEqual(sorted(rules.TOLERATED_TOWNS), ['SA'])
        for country in ('AE', 'EG', 'TR', None):
            self.assertTrue(rules.counts_against('county', country, 'town', 5000))


class PartOfALargerCityTest(unittest.TestCase):
    """An accepted area that lies inside a larger accepted area."""

    def test_the_outline_of_a_city_or_town_of_the_source_is_a_place_of_its_own(self):
        # Helwan inside the outline of Cairo, Floral Park inside the one of Hempstead.
        self.assertTrue(rules.is_place_of_its_own('locality', 'city'))
        self.assertTrue(rules.is_place_of_its_own('locality', 'town'))

    def test_an_outline_the_source_does_not_class_as_a_city_or_town_is_a_part_of_the_city(self):
        # Mitte inside Berlin, Manhattan inside New York.
        for place_class in (None, 'village', 'hamlet'):
            self.assertFalse(rules.is_place_of_its_own('locality', place_class))

    def test_a_district_or_region_inside_a_city_is_a_part_of_the_city(self):
        # A district of Bangkok inside Bangkok.
        for level in ('localadmin', 'county', 'region'):
            for place_class in (None, 'city', 'town'):
                self.assertFalse(rules.is_place_of_its_own(level, place_class))


class AreaKindTest(unittest.TestCase):
    """What an accepted area is called when it is not the outline of the city itself."""

    def test_a_saudi_governorate_is_named_as_a_governorate(self):
        self.assertEqual(rules.area_kind('county', 'SA'), 'governorate')

    def test_the_outline_of_a_saudi_place_itself_and_a_saudi_region_are_not(self):
        self.assertIsNone(rules.area_kind('locality', 'SA'))
        self.assertIsNone(rules.area_kind('localadmin', 'SA'))
        self.assertIsNone(rules.area_kind('region', 'SA'))

    def test_no_other_country_has_its_units_named(self):
        self.assertEqual(sorted(rules.AREA_KINDS), ['SA'])
        for country in ('AE', 'EG', 'TR', None):
            for level in rules.LEVELS:
                self.assertIsNone(rules.area_kind(level, country))


class ChoiceTest(unittest.TestCase):
    def test_the_smallest_area_that_carries_the_name_is_the_one_of_the_city(self):
        emirate = Candidate('b', 'region', 2549.0, 19)
        city = Candidate('a', 'locality', 479.0, 0)
        self.assertEqual(rules.smallest([emirate, city]), city)
        self.assertEqual(rules.smallest([city, emirate]), city)

    def test_equal_areas_give_the_lowest_source_id_whatever_the_order(self):
        first, second = Candidate('b', 'county', 10.0, 0), Candidate('a', 'county', 10.0, 0)
        self.assertEqual(rules.smallest([first, second]), second)
        self.assertEqual(rules.smallest([second, first]), second)

    def test_no_candidate_gives_none(self):
        self.assertIsNone(rules.smallest([]))

    def test_an_area_named_by_two_cities_takes_the_most_populous(self):
        small, large = City(1, 298343, 'New Cairo City'), City(2, 300000, 'New Cairo')
        self.assertEqual(rules.label([small, large]), large)
        self.assertEqual(rules.label([large, small]), large)

    def test_equal_populations_give_the_lowest_city_id_whatever_the_order(self):
        first, second = City(7, 1000, 'Twin'), City(5, 1000, 'Twin')
        self.assertEqual(rules.label([first, second]), second)
        self.assertEqual(rules.label([second, first]), second)


if __name__ == '__main__':
    unittest.main()
