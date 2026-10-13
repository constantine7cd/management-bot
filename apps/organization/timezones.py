from functools import lru_cache

from django.core.exceptions import ValidationError
from timezonefinder import TimezoneFinder


@lru_cache(maxsize=1)
def _finder():
    return TimezoneFinder()


def timezone_for_coordinates(latitude, longitude):
    timezone_name = _finder().timezone_at(lng=float(longitude), lat=float(latitude))
    if not timezone_name:
        raise ValidationError("No IANA timezone could be resolved for these coordinates")
    return timezone_name
