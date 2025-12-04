"""Base scraper class with common functionality for all sources."""

import asyncio
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional, Tuple
from pydantic import BaseModel, Field
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, LLMConfig, BrowserConfig, CacheMode
from crawl4ai.extraction_strategy import LLMExtractionStrategy
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.phone_validator import PhoneValidator
from utils.logger import ScraperLogger


class PhoneExtractionResult(BaseModel):
    """Schema for LLM extraction result."""
    phone_number: Optional[str] = Field(None, description="Swiss phone number in format +41 XX XXX XX XX or 0XX XXX XX XX")
    business_name: Optional[str] = Field(None, description="Found business name")
    address: Optional[str] = Field(None, description="Found address")
    confidence: str = Field(..., description="high/medium/low")
    notes: Optional[str] = Field(None, description="Any relevant notes")


class BaseScraper(ABC):
    """Base class for all scrapers with common functionality."""
    
    def __init__(self, config: Dict, logger: ScraperLogger):
        """
        Initialize base scraper.
        
        Args:
            config: Configuration dictionary
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        self.source_name = self.__class__.__name__.replace('Scraper', '').lower()
        self.delay_seconds = config.get('delay_seconds', 2)
        self.max_attempts = config.get('retry', {}).get('max_attempts', 3)
        self.base_delay = config.get('retry', {}).get('base_delay', 1)
        self.exponential_backoff = config.get('retry', {}).get('exponential_backoff', True)
        
        # Setup LLM config
        llm_config = config.get('llm', {})
        provider = llm_config.get('provider', 'openai/gpt-4o-mini')
        api_token = llm_config.get('api_token')
        
        # Load from .llm.env if not provided
        if not api_token:
            # Try to load from .llm.env file in parent directory (crawl4ai root)
            project_root = Path(__file__).parent.parent.parent
            llm_env_path = project_root / '.llm.env'
            if llm_env_path.exists():
                with open(llm_env_path, 'r') as f:
                    for line in f:
                        if line.startswith('OPENAI_API_KEY='):
                            api_token = line.split('=', 1)[1].strip()
                            break
            # Fallback to environment variable
            if not api_token:
                api_token = os.getenv('OPENAI_API_KEY')
        
        self.llm_config = LLMConfig(provider=provider, api_token=api_token)
        self.phone_validator = PhoneValidator()
    
    @abstractmethod
    def construct_search_url(self, business_data: Dict) -> str:
        """
        Construct search URL for the business.
        
        Args:
            business_data: Business data dictionary
            
        Returns:
            Search URL
        """
        pass
    
    @abstractmethod
    def get_extraction_instruction(self, business_data: Dict) -> str:
        """
        Get LLM extraction instruction for this source.
        
        Args:
            business_data: Business data dictionary
            
        Returns:
            Instruction string for LLM
        """
        pass
    
    def extract_phone_number(self, result: Dict) -> Optional[PhoneExtractionResult]:
        """
        Extract phone number from LLM extraction result.
        
        Args:
            result: Result from Crawl4AI extraction
            
        Returns:
            PhoneExtractionResult or None
        """
        try:
            extracted_content = result.get('extracted_content')
            if not extracted_content:
                return None
            
            # Parse JSON if it's a string
            if isinstance(extracted_content, str):
                import json
                try:
                    extracted_content = json.loads(extracted_content)
                except json.JSONDecodeError:
                    # Try to extract phone from text directly
                    phone = self.phone_validator.extract_phone_from_text(extracted_content)
                    if phone:
                        return PhoneExtractionResult(
                            phone_number=phone,
                            confidence="low",
                            notes="Extracted from text"
                        )
                    return None
            
            # Handle list of results
            if isinstance(extracted_content, list):
                if extracted_content:
                    extracted_content = extracted_content[0]
                else:
                    return None
            
            # Extract phone number
            phone = extracted_content.get('phone_number') or extracted_content.get('phone')
            if not phone:
                return None
            
            # Validate and normalize
            is_valid, normalized = self.phone_validator.validate_and_normalize(phone)
            if not is_valid:
                return None
            
            return PhoneExtractionResult(
                phone_number=normalized,
                business_name=extracted_content.get('business_name'),
                address=extracted_content.get('address'),
                confidence=extracted_content.get('confidence', 'medium'),
                notes=extracted_content.get('notes')
            )
        
        except Exception as e:
            self.logger.debug(f"Error extracting phone from result: {e}")
            return None
    
    async def scrape(self, business_data: Dict) -> Tuple[Optional[str], Optional[str]]:
        """
        Scrape phone number for a business with retry logic.
        
        Args:
            business_data: Business data dictionary
            
        Returns:
            Tuple of (phone_number, confidence) or (None, None) if not found
        """
        url = self.construct_search_url(business_data)
        instruction = self.get_extraction_instruction(business_data)
        
        for attempt in range(self.max_attempts):
            try:
                # Rate limiting delay
                if attempt > 0:
                    delay = self.base_delay * (2 ** attempt) if self.exponential_backoff else self.base_delay
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(self.delay_seconds)
                
                # Setup browser and crawler config
                browser_config = BrowserConfig(headless=True)
                
                crawler_config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    word_count_threshold=1,
                    page_timeout=60000,
                    extraction_strategy=LLMExtractionStrategy(
                        llm_config=self.llm_config,
                        schema=PhoneExtractionResult.model_json_schema(),
                        extraction_type="schema",
                        instruction=instruction,
                        force_json_response=True,
                        verbose=False,
                    ),
                )
                
                # Run crawler
                async with AsyncWebCrawler(config=browser_config) as crawler:
                    result = await crawler.arun(url=url, config=crawler_config)
                    
                    if result.success:
                        extraction_result = self.extract_phone_number(result)
                        if extraction_result and extraction_result.phone_number:
                            return extraction_result.phone_number, extraction_result.confidence
                    
                    # If extraction failed, try direct text extraction
                    if result.markdown:
                        phone = self.phone_validator.extract_phone_from_text(result.markdown)
                        if phone:
                            return phone, "low"
                
            except Exception as e:
                error_msg = str(e)
                self.logger.debug(f"Attempt {attempt + 1}/{self.max_attempts} failed for {self.source_name}: {error_msg}")
                
                if attempt == self.max_attempts - 1:
                    self.logger.error(f"All attempts failed for {self.source_name}: {error_msg}")
                    return None, None
        
        return None, None
    
    def validate_address_match(self, found_address: Optional[str], 
                               business_data: Dict) -> bool:
        """
        Validate if found address matches business address (fuzzy match).
        
        Args:
            found_address: Address found on page
            business_data: Original business data
            
        Returns:
            True if addresses match
        """
        if not found_address:
            return False
        
        # Get business address components
        street = business_data.get('STREET', '').lower()
        city = business_data.get('MAIL_CITY', '').lower()
        zip_code = business_data.get('MAIL_ZIP', '').lower()
        
        found_lower = found_address.lower()
        
        # Check if city matches
        if city and city in found_lower:
            # Check if street matches (partial match)
            if street:
                street_words = street.split()
                if len(street_words) > 0:
                    # Check if at least one significant word from street is in found address
                    significant_words = [w for w in street_words if len(w) > 3]
                    if significant_words:
                        if any(word in found_lower for word in significant_words):
                            return True
                    else:
                        # Short street name, check full match
                        if street in found_lower:
                            return True
            else:
                # No street, just check city
                return True
        
        return False

