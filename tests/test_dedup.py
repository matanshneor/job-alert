from jobalert.config import CompanyConfig
from jobalert.dedup import build_alias_map, canonical_company, fingerprint, normalize_city, normalize_title


def test_title_noise_removed():
    assert normalize_title("Customer Service Coordinator (20001596)") == "customer service coordinator"
    assert normalize_title("Data Analyst (m/f/d)") == "data analyst"
    assert normalize_title("Data Analyst - Hybrid") == "data analyst hybrid"
    assert normalize_title("  Junior  Data-Analyst ") == "junior data analyst"


def test_hebrew_titles_kept():
    assert normalize_title("אנליסט/ית נתונים") == "אנליסט ית נתונים"


def test_city_variants_match():
    same = ["Tel Aviv", "Tel Aviv-Yafo, Israel", "Tel-Aviv, Israel", "Tel Aviv District, Israel", "TLV"]
    assert {normalize_city(c) for c in same} == {"tel aviv"}
    assert normalize_city("IL-Haifa") == "haifa"
    assert normalize_city("Petach Tikva, Israel") == "petah tikva"
    assert normalize_city(None) == ""


def test_company_aliases():
    companies = [CompanyConfig("wix", "Wix", "smartrecruiters", aliases=("wix.com", "wixcom"))]
    alias_map = build_alias_map(companies)
    assert canonical_company("Wix.com", alias_map) == "wix"
    assert canonical_company("WIX", alias_map) == "wix"
    assert canonical_company("Some Startup Ltd", alias_map) == "some startup ltd"


def test_same_job_from_two_sources_same_fingerprint():
    a = fingerprint("wix", "Data Analyst", "Tel Aviv-Yafo, Israel")
    b = fingerprint("wix", "Data Analyst (m/f)", "Tel Aviv")
    assert a == b
    assert a != fingerprint("wix", "Data Analyst", "Haifa")
