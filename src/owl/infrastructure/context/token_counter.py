"""Default ``TokenCounter``: characters ÷ 3.5 with a 15% safety margin (CONTRACT §10)."""

import math

CHARS_PER_TOKEN = 3.5
SAFETY_MARGIN = 1.15


class CharTokenCounter:
    def count(self, text: str) -> int:
        # Rounded first so float noise (e.g. 35 / 3.5 * 1.15 = 11.499999...) cannot shift the ceil.
        return math.ceil(round(len(text) / CHARS_PER_TOKEN * SAFETY_MARGIN, 9))
