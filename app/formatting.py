"""How amounts and numbers look outside the database."""

import re

_LAKH_GROUPS = re.compile(r"(\d)(?=(\d\d)+$)")
# Card numbers are 13 to 19 digits, maybe with spaces or dashes. UTRs are 12 digits and must stay
# readable, because customers quote them when they chase a failed payment.
_CARD_LIKE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def format_inr(amount: int) -> str:
    sign = "-" if amount < 0 else ""
    digits = str(abs(amount))
    if len(digits) > 3:
        digits = _LAKH_GROUPS.sub(r"\1,", digits[:-3]) + "," + digits[-3:]
    return f"{sign}Rs {digits}"


def mask_card_numbers(text: str) -> str:
    def last_four(match: re.Match) -> str:
        return "XXXX " + re.sub(r"\D", "", match.group())[-4:]

    return _CARD_LIKE.sub(last_four, text)
