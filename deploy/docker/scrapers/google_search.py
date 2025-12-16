"""Scraper for Google Search results with business info panels."""

from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus
from .base_scraper import BaseScraper


class GoogleSearchScraper(BaseScraper):
    """Scraper for Google Search results."""
    
    def construct_search_url(self, business_data: Dict) -> str:
        """Construct Google search URL."""
        query_str = self._get_query_string(business_data)
        query = quote_plus(query_str)
        url = f"https://www.google.com/search?q={query}"
        
        return url
    
    def get_extraction_instruction(self, business_data: Dict) -> str:
        """Get LLM extraction instruction for Google Search."""
        query_str = self._get_query_string(business_data)
        search_method = (business_data.get('_active_search_method') or business_data.get('_search_strategy', {}).get('search_method') or 'name_based')

        # Get normalized search names
        search_names = self._get_search_names(business_data)
        business_name = search_names[0] if search_names else self._get_primary_name(business_data)
        street = business_data.get('STREET', '').strip()
        city = business_data.get('MAIL_CITY', '').strip()
        standardized_address = business_data.get('standardized_address', '').strip()
        
        instruction = f"""Extract the phone number for the business "{business_name}" from this Google search results page.
        
Look for:
1. The Google Knowledge Panel or business info box on the right side of results
2. The business name matching "{business_name}"
3. Address matching "{standardized_address or (street + ', ' + city)}" (if available)
4. A Swiss phone number in format +41 XX XXX XX XX or 0XX XXX XX XX

Google often displays business information in a structured panel. Check for:
- Business listings in search results
- Knowledge panels
- "People also search for" sections
- Map results with business details

If multiple results are found, prioritize the one that matches the business name and address most closely.
The original query used was: "{query_str}"
The intended method is: "{search_method}" (name_based/address_based/hybrid)

Return the phone number, business name, address, status (open/closed/unknown), and confidence level (high/medium/low).
If no matching business is found, return null for phone_number.

{self._get_ad_filtering_instruction()}"""
        
        return instruction

    def _get_query_string(self, business_data: Dict) -> str:
        """Resolve the query string for this scrape attempt."""
        active = (business_data.get('_active_query') or "").strip()
        if active:
            return active

        strategy = business_data.get('_search_strategy') or {}
        primary = (strategy.get('primary_query') or "").strip()
        if primary:
            return primary

        # Fallback to legacy behavior if no strategy exists.
        name = self._get_primary_name(business_data)
        city = (business_data.get('MAIL_CITY', '') or '').strip()
        street = (business_data.get('STREET', '') or '').strip()
        parts = [p for p in (name, city, street) if p]
        return " ".join(parts).strip()

    def search_by_address(self, full_address: str, category_hint: Optional[str] = None) -> str:
        """Build a Google query string focused on an exact address."""
        parts = [p for p in ((category_hint or "").strip(), (full_address or "").strip()) if p]
        return " ".join(parts).strip()

    async def scrape(self, business_data: Dict, crawler=None) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[Dict]]:
        """
        Try primary + fallback queries (if provided by preprocessing) and return the first successful extraction.
        """
        strategy = business_data.get('_search_strategy') or {}
        primary = (strategy.get('primary_query') or "").strip()
        fallbacks: List[str] = list(strategy.get('fallback_queries') or [])

        # If preprocessing didn't build strategies, just use the base implementation.
        if not primary and not fallbacks:
            return await super().scrape(business_data, crawler=crawler)

        attempts = [q for q in [primary, *fallbacks] if q and isinstance(q, str)]
        # Default method (overridable per attempt)
        method = (strategy.get('search_method') or 'name_based')

        for q in attempts:
            attempt_data = dict(business_data)
            attempt_data['_active_query'] = q
            attempt_data['_active_search_method'] = method

            scrape_result = await super().scrape(attempt_data, crawler=crawler)
            phone, conf, url = scrape_result[:3]
            meta = scrape_result[3] if len(scrape_result) > 3 else None
            if phone:
                return phone, conf, url, meta

        return None, None, None, None

