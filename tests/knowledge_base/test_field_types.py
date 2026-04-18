import pytest
from src.tools.knowledge_base.field_types import (
    StringType,
    NarrativeType,
    DateType,
    CurrencyType,
    EnumType,
)

def test_string_type():
    st = StringType()
    assert st.normalize("  hello  ") == "hello"
    assert st.normalize(None) is None
    
    assert st.compare("Hello", "hello") is True
    assert st.compare("  Hello  ", "HELLO") is True
    assert st.compare("Hello", "World") is False
    assert st.compare(None, None) is True
    assert st.compare("Hello", None) is False

def test_narrative_type():
    nt = NarrativeType(prefix_length=10)
    assert nt.normalize("  Hello \n  world  ") == "Hello world"
    assert nt.normalize(None) is None
    
    # Prefix match
    assert nt.compare("Long narrative starting same", "Long narrative different end") is True
    assert nt.compare("Short", "Short") is True
    assert nt.compare("Different", "Narrative") is False
    assert nt.compare(None, None) is True
    assert nt.compare("Hello", None) is False

def test_date_type():
    dt = DateType()
    assert dt.normalize("2026-03-28") == "2026-03-28"
    assert dt.normalize("28.03.2026") == "2026-03-28"
    assert dt.normalize("28/03/2026") == "2026-03-28"
    assert dt.normalize("03/28/2026") == "2026-03-28"
    assert dt.normalize("March 28, 2026") == "2026-03-28"
    assert dt.normalize("28 March 2026") == "2026-03-28"
    assert dt.normalize("Mar 28, 2026") == "2026-03-28"
    assert dt.normalize("28 Mar 2026") == "2026-03-28"
    assert dt.normalize("20260328") == "2026-03-28"
    
    # Invalid date returns raw
    assert dt.normalize("invalid-date") == "invalid-date"
    assert dt.normalize(None) is None
    
    assert dt.compare("28/03/2026", "2026-03-28") is True
    assert dt.compare("28/03/2026", "2026-03-29") is False

def test_currency_type():
    ct = CurrencyType()
    # Code first
    assert ct.normalize("EUR 12,650") == "EUR 12650"
    assert ct.normalize("€12.650,00") == "EUR 12650"
    assert ct.normalize("£ 100") == "GBP 100"
    assert ct.normalize("$50.50") == "USD 50"  # decimals stripped
    
    # Amount first
    assert ct.normalize("12650 EUR") == "EUR 12650"
    assert ct.normalize("1,000,000 USD") == "USD 1000000"
    
    # Mixed/Unknown
    assert ct.normalize("Unknown 100") == "Unknown 100" # No code found, returns raw
    assert ct.normalize("100") == "100" # No code found, returns raw
    assert ct.normalize(None) is None
    
    assert ct.compare("€12,650", "12650 EUR") is True
    assert ct.compare("EUR 100", "USD 100") is False

def test_enum_type():
    et = EnumType(
        allowed_values=["Red", "Green", "Blue"],
        aliases={"R": "Red", "G": "Green"}
    )
    assert et.normalize("  Red  ") == "red"
    assert et.normalize("R") == "red"
    assert et.normalize("yellow") == "yellow" # Graceful degradation
    assert et.normalize(None) is None
    
    assert et.compare("R", "red") is True
    assert et.compare("Green", "G") is True
    assert et.compare("Red", "Blue") is False

def test_reprs():
    assert repr(StringType()) == "<FieldType:string>"
    assert repr(NarrativeType()) == "<FieldType:narrative>"

def test_none_comparisons():
    # Test compare with None for all types
    types = [StringType(), NarrativeType(), DateType(), CurrencyType(), EnumType(["A"])]
    for t in types:
        assert t.compare(None, None) is True
        assert t.compare("val", None) is False
        assert t.compare(None, "val") is False

def test_currency_edge_cases():
    ct = CurrencyType()
    # Invalid amount (no digits)
    assert ct.normalize("EUR") == "EUR"
    # ValueError path in _parse (though re.sub usually prevents this if digits are found)
    # But let's try to hit parsed is None path
    assert ct.normalize("No digits here") == "No digits here"

def test_none_normalization():
    types = [StringType(), NarrativeType(), DateType(), CurrencyType(), EnumType(["A"])]
    for t in types:
        assert t.normalize(None) is None
