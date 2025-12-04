"""Utility to normalize business data from CSV, handling duplicates and irrelevant fields."""

from typing import Dict, List, Set
import re


# Generic words that should be ignored in address/company name fields
GENERIC_WORDS = {
    'restaurant', 'restaurants', 'café', 'cafe', 'bistrot', 'brasserie',
    'bar', 'lounge', 'cocktail bar', 'tea-room', 'tea room',
    'pizzeria', 'boulangerie', 'bäckerei', 'bäckereien'
}


def normalize_business_data(business: Dict) -> Dict:
    """
    Normalize business data by:
    1. Removing duplicate values between ADDRESS_LINE1/ADDRESS_LINE2
    2. Removing generic words like "Restaurant" from ADDRESS_LINE2
    3. Removing duplicate values between COMPANY_NAME0/COMPANY_NAME1/COMPANY_NAME2
    4. Removing values that match between address lines and company names
    
    Args:
        business: Business data dictionary from CSV
        
    Returns:
        Normalized business dictionary with additional fields:
        - search_names: List of unique business names to search
        - search_addresses: List of unique addresses to search
    """
    # Extract fields
    address_line1 = (business.get('ADDRESS_LINE1', '') or '').strip()
    address_line2 = (business.get('ADDRESS_LINE2', '') or '').strip()
    company_name0 = (business.get('COMPANY_NAME0', '') or '').strip()
    company_name1 = (business.get('COMPANY_NAME1', '') or '').strip()
    company_name2 = (business.get('COMPANY_NAME2', '') or '').strip()
    
    # Normalize addresses
    search_addresses = []
    
    # Process ADDRESS_LINE1
    if address_line1:
        # Check if it's not a generic word
        if not _is_generic_word(address_line1):
            search_addresses.append(address_line1)
    
    # Process ADDRESS_LINE2
    if address_line2:
        # Skip if it's a generic word
        if _is_generic_word(address_line2):
            address_line2 = None
        # Skip if it's the same as ADDRESS_LINE1
        elif address_line2.lower() == address_line1.lower():
            address_line2 = None
        else:
            search_addresses.append(address_line2)
    
    # Normalize company names
    search_names = []
    all_company_names = [company_name0, company_name1, company_name2]
    seen_names = set()
    
    for name in all_company_names:
        if not name:
            continue
        
        name_lower = name.lower()
        
        # Skip if it's a generic word
        if _is_generic_word(name):
            continue
        
        # Skip if we've already seen this name (case-insensitive)
        if name_lower in seen_names:
            continue
        
        # Skip if it matches any address line (case-insensitive)
        if address_line1 and name_lower == address_line1.lower():
            continue
        if address_line2 and name_lower == address_line2.lower():
            continue
        
        search_names.append(name)
        seen_names.add(name_lower)
    
    # If no company names found, try using ADDRESS_LINE1 as fallback
    if not search_names and address_line1 and not _is_generic_word(address_line1):
        search_names.append(address_line1)
    
    # Create normalized business data
    normalized = business.copy()
    normalized['_search_names'] = search_names
    normalized['_search_addresses'] = search_addresses
    normalized['_primary_name'] = search_names[0] if search_names else ''
    normalized['_primary_address'] = search_addresses[0] if search_addresses else ''
    
    return normalized


def _is_generic_word(text: str) -> bool:
    """
    Check if text is a generic word that should be ignored.
    
    Args:
        text: Text to check
        
    Returns:
        True if text is a generic word
    """
    if not text:
        return False
    
    text_lower = text.lower().strip()
    
    # Check exact match
    if text_lower in GENERIC_WORDS:
        return True
    
    # Check if text contains only generic words (e.g., "Restaurant & Bar")
    words = re.split(r'[\s&,]+', text_lower)
    if len(words) <= 3:  # Short phrases
        if all(word in GENERIC_WORDS or not word for word in words):
            return True
    
    return False


def get_search_queries(business: Dict) -> List[Dict]:
    """
    Generate search query combinations from normalized business data.
    
    Args:
        business: Normalized business dictionary
        
    Returns:
        List of search query dictionaries with name and address combinations
    """
    normalized = normalize_business_data(business)
    search_names = normalized.get('_search_names', [])
    search_addresses = normalized.get('_search_addresses', [])
    
    queries = []
    
    # If we have names and addresses, create combinations
    if search_names and search_addresses:
        for name in search_names:
            for address in search_addresses:
                queries.append({
                    'name': name,
                    'address': address,
                    'type': 'name_and_address'
                })
    # If we only have names
    elif search_names:
        for name in search_names:
            queries.append({
                'name': name,
                'address': None,
                'type': 'name_only'
            })
    # If we only have addresses (use as name)
    elif search_addresses:
        for address in search_addresses:
            queries.append({
                'name': address,
                'address': None,
                'type': 'address_as_name'
            })
    
    return queries

