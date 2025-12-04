"""Scraper for search.ch Swiss business directory."""

from typing import Dict
from urllib.parse import quote_plus
from .base_scraper import BaseScraper


class SearchChScraper(BaseScraper):
    """Scraper for search.ch directory."""
    
    def construct_search_url(self, business_data: Dict) -> str:
        """Construct search.ch search URL."""
        # Use normalized primary name
        name = self._get_primary_name(business_data)
        city = business_data.get('MAIL_CITY', '').strip()
        
        # Build search URL
        query = quote_plus(name)
        if city:
            url = f"https://www.search.ch/en/result?q={query}&loc={city}"
        else:
            url = f"https://www.search.ch/en/result?q={query}"
        
        return url
    
    def get_extraction_instruction(self, business_data: Dict) -> str:
        """Get LLM extraction instruction for search.ch."""
        # Get normalized search names
        search_names = self._get_search_names(business_data)
        business_name = search_names[0] if search_names else self._get_primary_name(business_data)
        street = business_data.get('STREET', '').strip()
        city = business_data.get('MAIL_CITY', '').strip()
        
        instruction = f"""Extract the phone number for the business "{business_name}" from this search.ch results page.
        
Look for:
1. The business name matching "{business_name}"
2. Address matching "{street}, {city}" (if available)
3. A Swiss phone number in format +41 XX XXX XX XX or 0XX XXX XX XX

If multiple results are found, prioritize the one that matches the business name and address most closely.
Return the phone number, business name, address, and confidence level (high/medium/low).
If no matching business is found, return null for phone_number.

{self._get_ad_filtering_instruction()}"""
        
        return instruction

