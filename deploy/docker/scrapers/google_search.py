"""Scraper for Google Search results with business info panels."""

from typing import Dict
from urllib.parse import quote_plus
from .base_scraper import BaseScraper


class GoogleSearchScraper(BaseScraper):
    """Scraper for Google Search results."""
    
    def construct_search_url(self, business_data: Dict) -> str:
        """Construct Google search URL."""
        # Use normalized primary name
        name = self._get_primary_name(business_data)
        city = business_data.get('MAIL_CITY', '').strip()
        street = business_data.get('STREET', '').strip()
        
        # Build search query
        query_parts = [name]
        if city:
            query_parts.append(city)
        if street:
            query_parts.append(street)
        
        query = quote_plus(" ".join(query_parts))
        url = f"https://www.google.com/search?q={query}"
        
        return url
    
    def get_extraction_instruction(self, business_data: Dict) -> str:
        """Get LLM extraction instruction for Google Search."""
        # Get normalized search names
        search_names = self._get_search_names(business_data)
        business_name = search_names[0] if search_names else self._get_primary_name(business_data)
        street = business_data.get('STREET', '').strip()
        city = business_data.get('MAIL_CITY', '').strip()
        
        instruction = f"""Extract the phone number for the business "{business_name}" from this Google search results page.
        
Look for:
1. The Google Knowledge Panel or business info box on the right side of results
2. The business name matching "{business_name}"
3. Address matching "{street}, {city}" (if available)
4. A Swiss phone number in format +41 XX XXX XX XX or 0XX XXX XX XX

Google often displays business information in a structured panel. Check for:
- Business listings in search results
- Knowledge panels
- "People also search for" sections
- Map results with business details

If multiple results are found, prioritize the one that matches the business name and address most closely.
Return the phone number, business name, address, and confidence level (high/medium/low).
If no matching business is found, return null for phone_number.

{self._get_ad_filtering_instruction()}"""
        
        return instruction

