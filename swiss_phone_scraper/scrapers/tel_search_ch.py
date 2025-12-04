"""Scraper for tel.search.ch phone directory."""

from typing import Dict
from urllib.parse import quote_plus
from .base_scraper import BaseScraper


class TelSearchChScraper(BaseScraper):
    """Scraper for tel.search.ch phone directory."""
    
    def construct_search_url(self, business_data: Dict) -> str:
        """Construct tel.search.ch search URL."""
        # Try business name first
        name = business_data.get('COMPANY_NAME0', '').strip()
        if not name:
            # Fallback to owner name
            firstname = business_data.get('FIRSTNAME', '').strip()
            lastname = business_data.get('LASTNAME', '').strip()
            name = f"{firstname} {lastname}".strip()
        
        city = business_data.get('MAIL_CITY', '').strip()
        
        # Build search URL
        query = quote_plus(name)
        if city:
            url = f"https://tel.search.ch/?was={query}&wo={city}"
        else:
            url = f"https://tel.search.ch/?was={query}"
        
        return url
    
    def get_extraction_instruction(self, business_data: Dict) -> str:
        """Get LLM extraction instruction for tel.search.ch."""
        business_name = business_data.get('COMPANY_NAME0', '').strip()
        street = business_data.get('STREET', '').strip()
        city = business_data.get('MAIL_CITY', '').strip()
        
        instruction = f"""Extract the phone number for the business "{business_name}" from this tel.search.ch phone directory page.
        
Look for:
1. The business name matching "{business_name}"
2. Address matching "{street}, {city}" (if available)
3. A Swiss phone number in format +41 XX XXX XX XX or 0XX XXX XX XX

This is a phone directory, so phone numbers should be prominently displayed.
If multiple results are found, prioritize the one that matches the business name and address most closely.
Return the phone number, business name, address, and confidence level (high/medium/low).
If no matching business is found, return null for phone_number."""
        
        return instruction

