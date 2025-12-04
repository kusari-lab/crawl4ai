"""Swiss phone number validation and normalization."""

import re
from typing import Optional, Tuple


class PhoneValidator:
    """Validates and normalizes Swiss phone numbers."""
    
    # Swiss phone number patterns
    # Format: +41 XX XXX XX XX or 0XX XXX XX XX
    # Area codes: 21-29, 31-39, 41-49, 51-59, 61-69, 71-79, 81-89, 91-99
    SWISS_AREA_CODES = {
        '21', '22', '24', '26', '27', '31', '32', '33', '34', '35', '36', '37', '38', '39',
        '41', '42', '43', '44', '45', '46', '47', '48', '49',
        '51', '52', '53', '54', '55', '56', '57', '58', '59',
        '61', '62', '63', '64', '65', '66', '67', '68', '69',
        '71', '72', '73', '74', '75', '76', '77', '78', '79',
        '81', '82', '83', '84', '85', '86', '87', '88', '89',
        '91', '92', '93', '94', '95', '96', '97', '98', '99'
    }
    
    @staticmethod
    def normalize_phone(phone: str) -> Optional[str]:
        """
        Normalize phone number to standard format: +41 XX XXX XX XX
        
        Args:
            phone: Raw phone number string
            
        Returns:
            Normalized phone number or None if invalid
        """
        if not phone:
            return None
            
        # Remove all non-digit characters except +
        cleaned = re.sub(r'[^\d+]', '', phone.strip())
        
        # Remove leading zeros if present after country code
        if cleaned.startswith('+41'):
            cleaned = cleaned[3:]  # Remove +41
            if cleaned.startswith('0'):
                cleaned = cleaned[1:]  # Remove leading 0
            cleaned = '+41' + cleaned
        elif cleaned.startswith('0041'):
            cleaned = cleaned[4:]  # Remove 0041
            if cleaned.startswith('0'):
                cleaned = cleaned[1:]  # Remove leading 0
            cleaned = '+41' + cleaned
        elif cleaned.startswith('41') and len(cleaned) > 2:
            # Check if it's a full number (not just area code)
            if len(cleaned) >= 9:  # At least area code + 7 digits
                if cleaned[2] == '0':
                    cleaned = cleaned[:2] + cleaned[3:]  # Remove 0 after 41
                cleaned = '+' + cleaned
        elif cleaned.startswith('0') and len(cleaned) >= 9:
            # Local format: 0XX XXX XX XX
            cleaned = '+41' + cleaned[1:]  # Replace 0 with +41
        
        # Validate format
        if PhoneValidator.is_valid(cleaned):
            return cleaned
        
        return None
    
    @staticmethod
    def is_valid(phone: str) -> bool:
        """
        Validate Swiss phone number format.
        
        Args:
            phone: Phone number to validate
            
        Returns:
            True if valid Swiss phone format
        """
        if not phone:
            return False
        
        # Check international format: +41 XX XXX XX XX
        intl_pattern = r'^\+41\s?([2-9]\d)\s?(\d{3})\s?(\d{2})\s?(\d{2})$'
        match = re.match(intl_pattern, phone.replace(' ', ''))
        
        if match:
            area_code = match.group(1)
            return area_code in PhoneValidator.SWISS_AREA_CODES
        
        # Check local format: 0XX XXX XX XX
        local_pattern = r'^0([2-9]\d)\s?(\d{3})\s?(\d{2})\s?(\d{2})$'
        match = re.match(local_pattern, phone.replace(' ', ''))
        
        if match:
            area_code = match.group(1)
            return area_code in PhoneValidator.SWISS_AREA_CODES
        
        return False
    
    @staticmethod
    def extract_phone_from_text(text: str) -> Optional[str]:
        """
        Extract and normalize first valid Swiss phone number from text.
        
        Args:
            text: Text to search for phone numbers
            
        Returns:
            Normalized phone number or None
        """
        if not text:
            return None
        
        # Patterns to match Swiss phone numbers
        patterns = [
            r'\+41\s?[2-9]\d\s?\d{3}\s?\d{2}\s?\d{2}',  # +41 XX XXX XX XX
            r'0[2-9]\d\s?\d{3}\s?\d{2}\s?\d{2}',  # 0XX XXX XX XX
            r'0041\s?[2-9]\d\s?\d{3}\s?\d{2}\s?\d{2}',  # 0041 XX XXX XX XX
            r'\+41\s?[2-9]\d\s?\d{3}\s?\d{2}\s?\d{2}',  # +41 XX XXX XX XX (no spaces)
            r'0[2-9]\d\s?\d{3}\s?\d{2}\s?\d{2}',  # 0XX XXX XX XX (no spaces)
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text.replace(' ', '').replace('-', ''))
            for match in matches:
                normalized = PhoneValidator.normalize_phone(match)
                if normalized:
                    return normalized
        
        return None
    
    @staticmethod
    def validate_and_normalize(phone: str) -> Tuple[bool, Optional[str]]:
        """
        Validate and normalize phone number.
        
        Args:
            phone: Phone number to validate and normalize
            
        Returns:
            Tuple of (is_valid, normalized_phone)
        """
        normalized = PhoneValidator.normalize_phone(phone)
        if normalized:
            return True, normalized
        return False, None

