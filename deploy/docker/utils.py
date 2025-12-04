import dns.resolver
import logging
import yaml
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from fastapi import Request
from typing import Dict, Optional

class TaskStatus(str, Enum):
    PROCESSING = "processing"
    FAILED = "failed"
    COMPLETED = "completed"

class FilterType(str, Enum):
    RAW = "raw"
    FIT = "fit"
    BM25 = "bm25"
    LLM = "llm"

def load_config() -> Dict:
    """Load and return application configuration with environment variable overrides."""
    config_path = Path(__file__).parent / "config.yml"
    with open(config_path, "r") as config_file:
        config = yaml.safe_load(config_file)
    
    # Override LLM provider from environment if set
    llm_provider = os.environ.get("LLM_PROVIDER")
    if llm_provider:
        config["llm"]["provider"] = llm_provider
        logging.info(f"LLM provider overridden from environment: {llm_provider}")
    
    # Also support direct API key from environment if the provider-specific key isn't set
    llm_api_key = os.environ.get("LLM_API_KEY")
    if llm_api_key and "api_key" not in config["llm"]:
        config["llm"]["api_key"] = llm_api_key
        logging.info("LLM API key loaded from LLM_API_KEY environment variable")
    
    return config

def setup_logging(config: Dict) -> None:
    """Configure application logging."""
    logging.basicConfig(
        level=config["logging"]["level"],
        format=config["logging"]["format"]
    )

def get_base_url(request: Request) -> str:
    """Get base URL including scheme and host."""
    return f"{request.url.scheme}://{request.url.netloc}"

def is_task_id(value: str) -> bool:
    """Check if the value matches task ID pattern."""
    return value.startswith("llm_") and "_" in value

def datetime_handler(obj: any) -> Optional[str]:
    """Handle datetime serialization for JSON."""
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def should_cleanup_task(created_at: str, ttl_seconds: int = 3600) -> bool:
    """Check if task should be cleaned up based on creation time."""
    created = datetime.fromisoformat(created_at)
    return (datetime.now() - created).total_seconds() > ttl_seconds

def decode_redis_hash(hash_data: Dict[bytes, bytes]) -> Dict[str, str]:
    """Decode Redis hash data from bytes to strings."""
    return {k.decode('utf-8'): v.decode('utf-8') for k, v in hash_data.items()}



def get_llm_api_key(config: Dict, provider: Optional[str] = None) -> str:
    """Get the appropriate API key based on the LLM provider.
    
    Args:
        config: The application configuration dictionary
        provider: Optional provider override (e.g., "openai/gpt-4")
    
    Returns:
        The API key for the provider, or empty string if not found
    """
        
    # Use provided provider or fall back to config
    if not provider:
        provider = config["llm"]["provider"]
    
    # Check if direct API key is configured
    if "api_key" in config["llm"]:
        return config["llm"]["api_key"]
    
    # Fall back to the configured api_key_env if no match
    return os.environ.get(config["llm"].get("api_key_env", ""), "")


def validate_llm_provider(config: Dict, provider: Optional[str] = None) -> tuple[bool, str]:
    """Validate that the LLM provider has an associated API key.
    
    Args:
        config: The application configuration dictionary
        provider: Optional provider override (e.g., "openai/gpt-4")
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Use provided provider or fall back to config
    if not provider:
        provider = config["llm"]["provider"]
    
    # Get the API key for this provider
    api_key = get_llm_api_key(config, provider)
    
    if not api_key:
        return False, f"No API key found for provider '{provider}'. Please set the appropriate environment variable."
    
    return True, ""


def verify_email_domain(email: str) -> bool:
    try:
        domain = email.split('@')[1]
        # Try to resolve MX records for the domain.
        records = dns.resolver.resolve(domain, 'MX')
        return True if records else False
    except Exception as e:
        return False


# Swiss Phone Validator
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
        """Normalize phone number to standard format: +41 XX XXX XX XX"""
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
        """Validate Swiss phone number format."""
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
        """Extract and normalize first valid Swiss phone number from text."""
        if not text:
            return None
        
        # Patterns to match Swiss phone numbers
        patterns = [
            r'\+41\s?[2-9]\d\s?\d{3}\s?\d{2}\s?\d{2}',  # +41 XX XXX XX XX
            r'0[2-9]\d\s?\d{3}\s?\d{2}\s?\d{2}',  # 0XX XXX XX XX
            r'0041\s?[2-9]\d\s?\d{3}\s?\d{2}\s?\d{2}',  # 0041 XX XXX XX XX
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
        """Validate and normalize phone number."""
        normalized = PhoneValidator.normalize_phone(phone)
        if normalized:
            return True, normalized
        return False, None