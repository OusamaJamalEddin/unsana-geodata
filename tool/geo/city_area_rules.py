"""Which administrative area of the source may stand for a bundled city
(DEC-049 as corrected on 2026-10-10).

The source, the divisions of Overture Maps, draws administrative areas at
several levels. None of them means "city" in every country: a city can be a
place of its own (a commune, a municipality, an incorporated place), a
district, a governorate, or a whole state. So the level is not fixed. The
rules below pick, for each bundled city, the area that carries its name, and
refuse an area that is more than that city.

  1. Levels. Only locality, localadmin, county and region. A neighborhood is
     a part of a place and a country is not a city; they are never used.
  2. Name. The area must carry a name of the city: equal after removing
     case, marks, spaces and punctuation, or equal once the word for the kind
     of unit ("Governorate", "City", and the like) is removed from the name
     of the area. The name of a city is never shortened.
  3. Smallest. Of the areas that hold the city's position and carry its
     name, the smallest is taken.
  4. Nothing else inside. A locality is the outline of the place itself and
     is accepted. A unit above it is accepted only when the source lists no
     other city or town inside it. An emirate, governorate or district that
     holds a second city or town is never relabeled as a city.
  5. Country rules (TOLERATED_TOWNS). Rule 4 is the same for every country
     but one. In Saudi Arabia the source draws the large cities only as
     governorates, and a governorate also holds the small towns around its
     city. There a town of fewer than 50,000 people, or of unknown size,
     does not refuse the governorate; another city, or a larger town, still
     does. So the governorate of Jeddah, with Thuwal, stands for Jeddah, and
     the governorate of Dammam, with Dhahran, stands for no city. The small
     towns inside take the name of the city: a known merge, listed by the
     build. No other country has such a rule: measured over the world,
     allowing towns gave more towns a wrong city name than it gave cities a
     boundary.
  6. A part of a city is not a city. Accepted areas can lie inside one
     another: a borough with an outline of its own inside its city, a town
     inside the outline of a larger one. The inner area keeps its boundary
     only when it is the outline of a place the source classes as a city or
     town. Anything else there is a part of the larger city and is left
     out, so the city's own name is given all over it: central Berlin is
     Berlin, not the borough of Mitte.
  7. An area is named as what it is (AREA_KINDS). Where the accepted area of
     a city is a Saudi governorate, the file says so, and the app names a GPS
     location inside it as the governorate, for example "Jeddah Governorate",
     not as the city. The user asked for this on 2026-10-10. It changes the
     label only: the area still stands for its city, and the position used
     for calculation is the GPS position, never a center of the governorate.
     Only Saudi governorates are named this way.

A city with no accepted area has no boundary: the app answers "city not
identified" there and offers the manual selection.

Python 3 standard library only. Tests: test_city_area_rules.py.
"""
import collections
import unicodedata

LEVELS = ('locality', 'localadmin', 'county', 'region')
# The outline of a populated place itself.
PLACE_LEVEL = 'locality'
# The classes of place that make a unit above PLACE_LEVEL more than one city.
OTHER_PLACE_CLASSES = ('city', 'town')
CITY_CLASS = 'city'
# Country, then level: a town of fewer people than this, or of unknown size,
# inside a unit of that level does not count against it (rule 5).
TOLERATED_TOWNS = {'SA': {'county': 50000}}
# Country, then level: what an accepted area of that level is named as (rule 7).
AREA_KINDS = {'SA': {'county': 'governorate'}}

City = collections.namedtuple('City', 'city_id population name')
Candidate = collections.namedtuple('Candidate', 'source_id level area_km2 other_places')

# Letters that Unicode does not decompose, and the Arabic letter forms that
# the bundled search terms write alike.
_LETTERS = {
    'ı': 'i', 'İ': 'i', 'ß': 'ss', 'æ': 'ae', 'Æ': 'ae', 'œ': 'oe', 'Œ': 'oe', 'ø': 'o', 'Ø': 'o',
    'đ': 'd', 'Đ': 'd', 'ð': 'd', 'Ð': 'd', 'ł': 'l', 'Ł': 'l', 'þ': 'th', 'Þ': 'th', 'ħ': 'h', 'Ħ': 'h',
    'ٱ': 'ا',  # alef wasla
    'ى': 'ي',  # alef maksura
    'ی': 'ي',  # Farsi yeh
    'ک': 'ك',  # keheh
    'ة': 'ه',  # ta marbuta
    'ـ': '',   # tatweel
}

# The words for a kind of unit that may be removed from the name of an area.
GENERIC_WORDS = (
    'city', 'municipality', 'governorate', 'emirate', 'district', 'province', 'metropolitan',
    'مدينة',    # madina: city
    'بلدية',    # baladiya: municipality
    'أمانة',    # amana: municipality of a large city
    'محافظة',   # muhafaza: governorate
    'إمارة',    # imara: emirate
    'منطقة',    # mintaqa: district or region
    'مركز',     # markaz: district
    'قسم',      # qism: district
    'لواء',     # liwa: district
    'قضاء',     # qada: district
    'مقاطعة',   # muqataa: province
    'ولاية',    # wilaya: province
    'الكبرى',   # al kubra: greater
    'kota',     # Indonesian: city
    'stadt',    # German: city
    'merkez',   # Turkish: central district
    'tehsil',   # Pakistan: subdivision of a district
    'taluka',   # Pakistan: subdivision of a district
)
CONNECTIVES = ('of', 'the')


def _folded(text):
    """Lower case, without marks; separators are kept."""
    replaced = ''.join(_LETTERS.get(character, character) for character in text)
    decomposed = unicodedata.normalize('NFKD', replaced.lower())
    return ''.join(character for character in decomposed if unicodedata.category(character) != 'Mn')


def _is_word_character(character):
    return unicodedata.category(character)[0] in ('L', 'N')


def _words(folded):
    words, word = [], []
    for character in folded:
        if _is_word_character(character):
            word.append(character)
        elif word:
            words.append(''.join(word))
            word = []
    if word:
        words.append(''.join(word))
    return words


def normalized_name(name):
    """A name for comparison: letters and digits only, without case or marks."""
    return ''.join(_words(_folded(name or '')))


_GENERIC = frozenset(normalized_name(word) for word in GENERIC_WORDS)


def name_without_generic_words(name):
    """The name without the words for its kind of unit; None when it has no such word or nothing else."""
    words = _words(_folded(name or ''))
    if not any(word in _GENERIC for word in words):
        return None
    kept = [word for word in words if word not in _GENERIC and word not in CONNECTIVES]
    return ''.join(kept) or None


def place_names(names):
    """The names of a city or town, for comparison. They are never shortened."""
    return frozenset(key for key in (normalized_name(name) for name in names) if key)


def area_names(names):
    """The names an area is known by: each name, and each name without its generic words."""
    shortened = (name_without_generic_words(name) for name in names)
    return place_names(names) | frozenset(key for key in shortened if key)


def same_name(area, place):
    """True when the area ([area_names]) carries a name of the place ([place_names])."""
    return not area.isdisjoint(place)


def counts_against(level, country, place_class, population):
    """True when another place of the source, inside a unit of [level] in [country], makes it more than one city.

    [population] is the source's own figure, None when it gives none.
    """
    if place_class not in OTHER_PLACE_CLASSES:
        return False
    limit = TOLERATED_TOWNS.get(country, {}).get(level)
    if limit is None or place_class == CITY_CLASS:
        return True
    return population is not None and population >= limit


def area_kind(level, country):
    """What an accepted area of [level] in [country] is named as (rule 7); None when it is named as its city."""
    return AREA_KINDS.get(country, {}).get(level)


def is_place_of_its_own(level, place_class):
    """True for an accepted area that keeps its boundary inside a larger accepted area (rule 6).

    [place_class] is what the source calls the place the area belongs to,
    None when it gives no class.
    """
    return level == PLACE_LEVEL and place_class in OTHER_PLACE_CLASSES


def is_accepted(level, other_places):
    """True when an area of [level] that holds [other_places] other cities and towns may stand for its city."""
    if level not in LEVELS:
        return False
    return level == PLACE_LEVEL or other_places == 0


def smallest(candidates):
    """The smallest candidate; among equals, the lowest source id. None without candidates."""
    return min(candidates, key=lambda candidate: (candidate.area_km2, candidate.source_id), default=None)


def label(cities):
    """The city that labels an area several cities name: the most populous; among equals, the lowest id."""
    return min(cities, key=lambda city: (-city.population, city.city_id))
