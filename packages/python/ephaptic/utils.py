import re

def parse_limit(limit: str) -> tuple[int, int]:
    count, period = limit.replace(' per ', '/').split('/')
    count = int(count)

    match = re.fullmatch(r'(\d+)?\s*([smhd])', period.lower())
    if not match:
        raise ValueError(f"Invalid rate limit period: {period}")

    multiplier = int(match.group(1) or 1)
    unit = match.group(2)

    s = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400,
    }[unit]

    return count, multiplier * s