import re

_UNITS = {
    'seconds': 1, 'second': 1, 'secs': 1, 'sec': 1, 's': 1,
    'minutes': 60, 'minute': 60, 'mins': 60, 'min': 60, 'm': 60,
    'hours': 3600, 'hour': 3600, 'hrs': 3600, 'hr': 3600, 'h': 3600,
    'days': 86400, 'day': 86400, 'd': 86400,
}

def parse_limit(limit: str) -> tuple[int, int]:
    if not isinstance(limit, str):
        raise ValueError(f"Invalid rate limit: {limit!r}")

    count_part, sep, period = limit.replace(' per ', '/').partition('/')
    if not sep:
        raise ValueError(f"Invalid rate limit: {limit!r} (expected a form like '5/m' or '10 per 30s')")

    try:
        count = int(count_part.strip())
    except ValueError:
        raise ValueError(f"Invalid rate limit count: {count_part.strip()!r}") from None

    match = re.fullmatch(r'(\d+)?\s*([a-z]+)', period.strip().lower())
    if not match:
        raise ValueError(f"Invalid rate limit period: {period.strip()!r}")

    multiplier = int(match.group(1) or 1)
    unit = match.group(2)
    if unit not in _UNITS:
        raise ValueError(
            f"Invalid rate limit unit: {unit!r} (expected one of s, m, h, d or their full names)"
        )

    return count, multiplier * _UNITS[unit]


class UnsupportedIdentityError(TypeError):
    """Raised when an identity cannot be reduced to a key injectively."""

    def __init__(self, identity):
        super().__init__(
            f"An identity of type {type(identity).__name__!r} cannot be reduced to a key."
             "Return a string, integer, or UUID from the identity loader."
        )


def identity_key(identity) -> str:
    if isinstance(identity, str):
        return 's:' + identity
    # bool subclasses int, so it is tested first to keep True from becoming 1.
    if isinstance(identity, bool):
        return 'b:1' if identity else 'b:0'
    if isinstance(identity, int):
        return 'i:' + str(identity)

    from uuid import UUID
    if isinstance(identity, UUID):
        return 'u:' + str(identity)

    raise UnsupportedIdentityError(identity)