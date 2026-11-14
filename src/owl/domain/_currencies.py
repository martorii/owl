"""ISO 4217 currency resolution (internal to the domain).

Output is always the three-letter ISO code. Symbols that are genuinely ambiguous (``kr``, ``Fr``,
``dollar`` variants not listed here) are *not* guessed: ``$`` means USD, ``¥`` means JPY.
"""

import re
import unicodedata

CURRENCY_CODES: frozenset[str] = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN
    BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD
    FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY
    KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP
    MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR
    RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT
    TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XCG XOF XPF YER ZAR
    ZMW ZWG
    """.split()  # noqa: SIM905
)

_ALIASES: dict[str, str] = {
    # symbols
    "$": "USD",
    "us$": "USD",
    "usd$": "USD",
    "u$s": "USD",
    "a$": "AUD",
    "au$": "AUD",
    "c$": "CAD",
    "ca$": "CAD",
    "can$": "CAD",
    "nz$": "NZD",
    "hk$": "HKD",
    "s$": "SGD",
    "r$": "BRL",
    "mx$": "MXN",
    "nt$": "TWD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "rmb": "CNY",
    "cn¥": "CNY",
    "₹": "INR",
    "₩": "KRW",
    "₺": "TRY",
    "₽": "RUB",
    "₪": "ILS",
    "₫": "VND",
    "₱": "PHP",
    "฿": "THB",
    "zł": "PLN",
    "kč": "CZK",
    "sfr": "CHF",
    "fr.": "CHF",
    # names (casefolded, whitespace collapsed)
    "euro": "EUR",
    "euros": "EUR",
    "dollar": "USD",
    "dollars": "USD",
    "us dollar": "USD",
    "us dollars": "USD",
    "usdollar": "USD",
    "canadian dollar": "CAD",
    "canadian dollars": "CAD",
    "australian dollar": "AUD",
    "australian dollars": "AUD",
    "pound": "GBP",
    "pounds": "GBP",
    "pound sterling": "GBP",
    "pounds sterling": "GBP",
    "sterling": "GBP",
    "pfund": "GBP",
    "livre sterling": "GBP",
    "libra esterlina": "GBP",
    "yen": "JPY",
    "yuan": "CNY",
    "renminbi": "CNY",
    "swiss franc": "CHF",
    "swiss francs": "CHF",
    "franken": "CHF",
    "schweizer franken": "CHF",
    "rupee": "INR",
    "rupees": "INR",
    "rand": "ZAR",
}

_WS = re.compile(r"\s+")


def _key(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).strip().casefold())


def resolve_currency(token: str) -> str | None:
    """Return the ISO code for a single code, symbol or name; ``None`` if unknown."""
    key = _key(token)
    if len(key) == 3 and key.isalpha() and key.upper() in CURRENCY_CODES:
        return key.upper()
    return _ALIASES.get(key)


def resolve_currency_text(text: str) -> str | None:
    """Resolve free text around an amount (``"US dollars"``, ``"usd $"``).

    Tries the whole phrase first, then each token; all tokens must agree on one currency.
    """
    key = _key(text).strip(" .,")
    if not key:
        return None
    if (whole := resolve_currency(key)) is not None:
        return whole
    resolved = {resolve_currency(token) for token in key.split(" ")}
    if len(resolved) == 1:
        return resolved.pop()
    return None
