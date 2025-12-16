import importlib.util
from pathlib import Path


def _load_business_preprocessor_module():
    repo_root = Path(__file__).resolve().parents[2]
    mod_path = repo_root / "deploy" / "docker" / "utils" / "business_preprocessor.py"
    spec = importlib.util.spec_from_file_location("business_preprocessor", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def test_preprocess_commercial_name_high_quality():
    mod = _load_business_preprocessor_module()
    row = {
        "COMPANY_NAME0": "ABC Restaurants Sàrl",
        "ADDRESS_LINE1": "Rue de la Gare 5",
        "ADDRESS_LINE2": "",
        "ZIP": "1000",
        "MAIL_CITY": "Lausanne",
    }
    enriched = mod.preprocess_business_row(row)
    assert enriched["is_commercial_name"] is True
    assert enriched["legal_form"].lower() in ("sàrl", "sarl")
    assert enriched["cleaned_name"] == "ABC Restaurants"
    assert enriched["data_quality"] == "HIGH"
    assert enriched["search_priority"] == 0
    assert enriched["_search_strategy"]["search_method"] == "hybrid"
    assert "Rue de la Gare 5" in enriched["_search_strategy"]["primary_query"]


def test_preprocess_person_name_low_quality():
    mod = _load_business_preprocessor_module()
    row = {
        "COMPANY_NAME0": "Monsieur Ali Öztürk",
        "ADDRESS_LINE1": "Chemin des Fleurs 10",
        "ZIP": "1000",
        "MAIL_CITY": "Lausanne",
    }
    enriched = mod.preprocess_business_row(row)
    assert enriched["is_commercial_name"] is False
    assert enriched["name_type"] in ("person", "unknown")
    assert enriched["cleaned_name"] == "Ali Öztürk"
    assert enriched["data_quality"] in ("LOW", "MEDIUM")
    assert enriched["_search_strategy"]["search_method"] == "address_based"
    assert "Chemin des Fleurs 10" in enriched["_search_strategy"]["primary_query"]


def test_address_standardization_removes_case_postale():
    mod = _load_business_preprocessor_module()
    row = {
        "COMPANY_NAME0": "Test SA",
        "ADDRESS_LINE1": "Case postale 123",
        "ADDRESS_LINE2": "Rue du Lac 1",
        "ZIP": "1200",
        "MAIL_CITY": "Genève",
    }
    enriched = mod.preprocess_business_row(row)
    assert "case postale" not in enriched["standardized_address"].lower()
    assert "Rue du Lac 1" in enriched["standardized_address"]


def test_address_similarity_normalization():
    # Avoid BaseScraper.__init__ by bypassing constructor (no LLM config needed for this unit test)
    repo_root = Path(__file__).resolve().parents[2]
    base_path = repo_root / "deploy" / "docker" / "scrapers" / "base_scraper.py"
    spec = importlib.util.spec_from_file_location("base_scraper", base_path)
    base_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base_mod)  # type: ignore[attr-defined]
    BaseScraper = base_mod.BaseScraper

    class _Dummy(BaseScraper):
        def construct_search_url(self, business_data):
            return ""

        def get_extraction_instruction(self, business_data):
            return ""

    dummy = object.__new__(_Dummy)
    business = {"standardized_address": "Rue de la Gare 5, 1000 Lausanne"}
    found = "Rue de la Gare 5 1000 Lausanne"
    sim = dummy.compute_address_similarity(found, business)
    assert sim > 0.9


