"""Business row pre-processing utilities for smarter search strategies.

This module enriches a single CSV row (dict) with:
- name parsing (commercial vs person), legal form extraction, cleaned name
- address standardization (remove case postale, normalize whitespace)
- data quality classification (HIGH/MEDIUM/LOW) and search priority
- per-row multi-strategy search queries under `_search_strategy`
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


_TITLE_PREFIXES = (
    "monsieur",
    "madame",
    "m.",
    "mme",
    "mlle",
    "mademoiselle",
    "dr",
    "docteur",
    "prof",
    "professeur",
    "herr",
    "frau",
    "signor",
    "signora",
)

# Keep this conservative; we only want strong signals.
_LEGAL_FORMS = (
    "sàrl",
    "sarl",
    "sa",
    "snc",
    "ag",
    "gmbh",
    "kg",
    "klg",
    "sagl",
    "ltd",
    "inc",
)

_LEGAL_FORM_RE = re.compile(
    r"(?i)\b(" + "|".join(re.escape(x) for x in _LEGAL_FORMS) + r")\b\.?"
)

_CASE_POSTALE_RE = re.compile(
    r"(?i)\b(case\s+postale|c\.?\s*p\.?|cp)\b[^\w]*\d*\b"
)

_WS_RE = re.compile(r"\s+")
_PUNCT_TRIM_RE = re.compile(r"^[\s,;:\-–—]+|[\s,;:\-–—]+$")


@dataclass(frozen=True)
class SearchStrategy:
    primary_query: str
    fallback_queries: List[str]
    search_method: str  # name_based | address_based | hybrid
    confidence_modifier: float

    def to_dict(self) -> Dict:
        return {
            "primary_query": self.primary_query,
            "fallback_queries": self.fallback_queries,
            "search_method": self.search_method,
            "confidence_modifier": self.confidence_modifier,
        }


def preprocess_business_row(business: Dict) -> Dict:
    """Return a copy of business row enriched with metadata."""
    row = dict(business or {})

    raw_name = _coalesce_name(row)
    cleaned_name, legal_form, name_type, is_commercial, name_notes = parse_business_name(raw_name)

    standardized_address, address_notes = standardize_address(row)
    has_addr = bool(standardized_address)
    has_legal_form = bool(legal_form)

    data_quality = classify_data_quality(
        is_commercial_name=is_commercial,
        has_legal_form=has_legal_form,
        has_address=has_addr,
    )
    search_priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(data_quality, 2)

    strategy = build_search_strategy(
        cleaned_name=cleaned_name,
        standardized_address=standardized_address,
        data_quality=data_quality,
        is_commercial_name=is_commercial,
        row=row,
    )

    row.update(
        {
            # required output cols
            "data_quality": data_quality,
            "is_commercial_name": bool(is_commercial),
            "cleaned_name": cleaned_name or "",
            "search_priority": search_priority,
            # extra metadata (useful for debugging / future tuning)
            "name_type": name_type,
            "legal_form": legal_form or "",
            "standardized_address": standardized_address or "",
            "name_quality_notes": name_notes or "",
            "address_quality_notes": address_notes or "",
            "_search_strategy": strategy.to_dict(),
        }
    )

    return row


def _coalesce_name(row: Dict) -> str:
    """Pick the best available name field from known schemas."""
    for k in ("COMPANY_NAME0", "COMPANY_NAME1", "COMPANY_NAME2", "ADDRESS_LINE2", "ADDRESS_LINE1"):
        v = (row.get(k, "") or "").strip()
        if v:
            return v
    firstname = (row.get("FIRSTNAME", "") or "").strip()
    lastname = (row.get("LASTNAME", "") or "").strip()
    return f"{firstname} {lastname}".strip()


def parse_business_name(raw: str) -> Tuple[str, Optional[str], str, bool, str]:
    """Parse raw name into cleaned name + metadata."""
    if not raw:
        return "", None, "unknown", False, "empty"

    s = _normalize_ws(raw)

    # Title stripping
    lowered = s.lower()
    title_stripped = s
    title_notes = ""
    for t in _TITLE_PREFIXES:
        if lowered.startswith(t + " ") or lowered == t:
            title_stripped = s[len(t) :].strip(" .,-–—")
            title_notes = f"stripped_title:{t}"
            break

    # Legal form extraction
    legal_form = None
    m = _LEGAL_FORM_RE.search(title_stripped)
    if m:
        legal_form = m.group(1)
    cleaned = _LEGAL_FORM_RE.sub("", title_stripped)
    cleaned = _PUNCT_TRIM_RE.sub("", cleaned)
    cleaned = _normalize_ws(cleaned)

    # Heuristics for person vs commercial
    is_person = False
    is_commercial = False
    notes = [n for n in (title_notes,) if n]

    if legal_form:
        is_commercial = True
        notes.append(f"legal_form:{legal_form}")

    # Two-word names with title or firstname/lastname style tend to be person names.
    # We keep this conservative to avoid misclassifying e.g. "Café Ali".
    tokens = [t for t in re.split(r"\s+", cleaned) if t]
    if not is_commercial:
        if title_notes:
            is_person = True
        elif 1 < len(tokens) <= 3 and all(_looks_like_person_token(t) for t in tokens):
            # If everything looks like a name token and no company markers, treat as person.
            is_person = True

    if is_person:
        name_type = "person"
    elif is_commercial:
        name_type = "commercial"
    else:
        name_type = "unknown"

    is_commercial_name = name_type == "commercial"
    notes_str = ",".join(notes) if notes else ""
    return cleaned, legal_form, name_type, is_commercial_name, notes_str


def _looks_like_person_token(tok: str) -> bool:
    # Basic heuristic: capitalized-like or contains diacritics; avoid digits and obvious business symbols.
    if not tok or any(ch.isdigit() for ch in tok):
        return False
    if any(ch in tok for ch in ("&", "@", "/", "\\")):
        return False
    return True


def standardize_address(row: Dict) -> Tuple[str, str]:
    """Standardize address using ADDRESS_LINE1/ADDRESS_LINE2 primarily (per user choice)."""
    parts: List[str] = []

    a1 = (row.get("ADDRESS_LINE1", "") or "").strip()
    a2 = (row.get("ADDRESS_LINE2", "") or "").strip()
    zip_code = (row.get("ZIP", "") or row.get("POSTAL_CODE", "") or "").strip()
    city = (row.get("MAIL_CITY", "") or row.get("CITY", "") or "").strip()

    notes: List[str] = []

    def clean_addr_piece(s: str) -> str:
        if not s:
            return ""
        s2 = _CASE_POSTALE_RE.sub("", s)
        s2 = _PUNCT_TRIM_RE.sub("", s2)
        s2 = _normalize_ws(s2)
        return s2

    a1c = clean_addr_piece(a1)
    a2c = clean_addr_piece(a2)
    if a1 and not a1c:
        notes.append("removed_case_postale:ADDRESS_LINE1")
    if a2 and not a2c:
        notes.append("removed_case_postale:ADDRESS_LINE2")

    if a1c:
        parts.append(a1c)
    if a2c and (not a1c or a2c.lower() != a1c.lower()):
        parts.append(a2c)

    # Append ZIP + city if present and not already part of the lines.
    locality = " ".join([p for p in (zip_code, city) if p]).strip()
    locality = _normalize_ws(locality)
    if locality:
        joined = " ".join(parts).lower()
        if locality.lower() not in joined:
            parts.append(locality)

    standardized = ", ".join([p for p in parts if p])
    standardized = _normalize_ws(standardized)
    return standardized, ",".join(notes) if notes else ""


def classify_data_quality(*, is_commercial_name: bool, has_legal_form: bool, has_address: bool) -> str:
    if is_commercial_name and has_legal_form and has_address:
        return "HIGH"
    if (is_commercial_name and has_address) or (is_commercial_name and has_legal_form) or (has_address and has_legal_form):
        return "MEDIUM"
    return "LOW"


def build_search_strategy(
    *,
    cleaned_name: str,
    standardized_address: str,
    data_quality: str,
    is_commercial_name: bool,
    row: Dict,
) -> SearchStrategy:
    """Build primary + fallback queries per row."""
    city = (row.get("MAIL_CITY", "") or row.get("CITY", "") or "").strip()
    city = _normalize_ws(city)

    # Optional category hint: try to infer from known text fields (very light).
    category_hint = _infer_category_hint(row)

    def uniq(qs: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for q in qs:
            qn = _normalize_ws(q)
            if not qn:
                continue
            key = qn.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(qn)
        return out

    if data_quality == "HIGH":
        primary = " ".join([cleaned_name, standardized_address]).strip()
        fallbacks = uniq(
            [
                " ".join([cleaned_name, city]).strip(),
                standardized_address,
                " ".join([category_hint, standardized_address]).strip() if category_hint else "",
            ]
        )
        return SearchStrategy(primary, fallbacks, "hybrid", 1.0)

    if data_quality == "MEDIUM":
        primary = " ".join([cleaned_name, city]).strip() if cleaned_name else standardized_address
        fallbacks = uniq(
            [
                " ".join([cleaned_name, standardized_address]).strip() if cleaned_name and standardized_address else "",
                standardized_address,
                " ".join([category_hint, standardized_address]).strip() if category_hint else "",
            ]
        )
        method = "hybrid" if cleaned_name and standardized_address else ("name_based" if cleaned_name else "address_based")
        return SearchStrategy(primary, fallbacks, method, 0.8)

    # LOW: ignore person/owner name; use address (most reliable).
    primary = " ".join([category_hint, standardized_address]).strip() if category_hint and standardized_address else standardized_address
    fallbacks = uniq(
        [
            standardized_address,
            " ".join([category_hint, city]).strip() if category_hint and city else "",
            city,
        ]
    )
    return SearchStrategy(primary, fallbacks, "address_based", 0.6)


def _infer_category_hint(row: Dict) -> str:
    """Best-effort category hint. Kept intentionally minimal to avoid false signals."""
    hay = " ".join(
        [
            (row.get("COMPANY_NAME0", "") or ""),
            (row.get("COMPANY_NAME1", "") or ""),
            (row.get("COMPANY_NAME2", "") or ""),
            (row.get("ADDRESS_LINE2", "") or ""),
        ]
    ).lower()
    for kw in ("restaurant", "cafe", "café", "pizzeria", "bar", "bistrot", "brasserie"):
        if kw in hay:
            return kw.title() if kw != "café" else "Café"
    return ""


def _normalize_ws(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip())


