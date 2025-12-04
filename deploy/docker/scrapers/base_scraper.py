"""Base scraper class with common functionality for all sources."""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple, List
from pydantic import BaseModel, Field
from crawl4ai import CrawlerRunConfig, LLMConfig, BrowserConfig, CacheMode
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from crawler_pool import get_crawler
from utils import PhoneValidator, get_llm_api_key
import sys
import os
from pathlib import Path

# Import business data normalizer
try:
    # Try importing from utils directory
    import importlib.util
    utils_path = Path(__file__).parent.parent / "utils" / "business_data_normalizer.py"
    if utils_path.exists():
        spec = importlib.util.spec_from_file_location("business_data_normalizer", utils_path)
        business_data_normalizer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(business_data_normalizer)
        normalize_business_data = business_data_normalizer.normalize_business_data
    else:
        raise ImportError("business_data_normalizer not found")
except (ImportError, Exception):
    # Fallback if import fails - define a simple version
    def normalize_business_data(business):
        """Fallback normalizer that just returns the business as-is."""
        # Add basic normalization
        result = business.copy()
        result['_search_names'] = [business.get('COMPANY_NAME0', '').strip()] if business.get('COMPANY_NAME0') else []
        result['_primary_name'] = result['_search_names'][0] if result['_search_names'] else ''
        result['_search_addresses'] = []
        result['_primary_address'] = ''
        return result


class PhoneExtractionResult(BaseModel):
    """Schema for LLM extraction result."""
    phone_number: Optional[str] = Field(None, description="Swiss phone number in format +41 XX XXX XX XX or 0XX XXX XX XX")
    business_name: Optional[str] = Field(None, description="Found business name")
    address: Optional[str] = Field(None, description="Found address")
    confidence: str = Field(..., description="high/medium/low")
    notes: Optional[str] = Field(None, description="Any relevant notes")


class PhoneValidationResult(BaseModel):
    """Schema for LLM validation result to check if phone is from ads."""
    is_legitimate: bool = Field(..., description="True if phone number is from the actual business, False if from ads/sponsored content")
    reason: str = Field(..., description="Explanation of why the phone is legitimate or from ads")
    confidence: str = Field(..., description="high/medium/low confidence in the validation")


class BaseScraper(ABC):
    """Base class for all scrapers with common functionality."""
    
    def __init__(self, config: Dict, app_config: Dict):
        """
        Initialize base scraper.
        
        Args:
            config: Source-specific configuration dictionary
            app_config: Application configuration (from config.yml)
        """
        self.config = config
        self.app_config = app_config
        self.logger = logging.getLogger(__name__)
        self.source_name = self.__class__.__name__.replace('Scraper', '').lower()
        self.delay_seconds = config.get('delay_seconds', 2)
        
        scraper_config = app_config.get('swiss_phone_scraper', {})
        retry_config = scraper_config.get('retry', {})
        self.max_attempts = retry_config.get('max_attempts', 3)
        self.base_delay = retry_config.get('base_delay', 1)
        self.exponential_backoff = retry_config.get('exponential_backoff', True)
        
        # Setup LLM config using app config
        llm_config = app_config.get('llm', {})
        provider = llm_config.get('provider', 'openai/gpt-4o-mini')
        api_token = get_llm_api_key(app_config, provider)
        
        self.llm_config = LLMConfig(provider=provider, api_token=api_token)
        self.phone_validator = PhoneValidator()
    
    def _normalize_business(self, business_data: Dict) -> Dict:
        """Normalize business data to handle duplicates and generic words."""
        return normalize_business_data(business_data)
    
    def _get_primary_name(self, business_data: Dict) -> str:
        """Get primary business name from normalized data."""
        normalized = self._normalize_business(business_data)
        name = normalized.get('_primary_name', '').strip()
        if not name:
            name = business_data.get('COMPANY_NAME0', '').strip()
        if not name:
            name = business_data.get('ADDRESS_LINE1', '').strip()
        if not name:
            firstname = business_data.get('FIRSTNAME', '').strip()
            lastname = business_data.get('LASTNAME', '').strip()
            name = f"{firstname} {lastname}".strip()
        return name
    
    def _get_search_names(self, business_data: Dict) -> List[str]:
        """Get all unique search names from normalized data."""
        normalized = self._normalize_business(business_data)
        return normalized.get('_search_names', [])
    
    def _get_ad_filtering_instruction(self) -> str:
        """Get standard instruction text for filtering ads and sponsored content."""
        return """
IMPORTANT: IGNORE ADS AND SPONSORED CONTENT
- Do NOT extract phone numbers from advertisements, sponsored listings, or promotional content
- Do NOT extract phone numbers from "Ads" sections, "Sponsored" labels, or "Advertisement" banners
- Do NOT extract phone numbers from pop-ups, overlays, or promotional popups
- Only extract phone numbers from the actual business listing or search result
- If a phone number appears in an ad section, skip it even if it matches the business name
- Look for visual indicators like "Ad", "Sponsored", "Advertisement", "Promoted" labels
- Prioritize phone numbers from the main search results, not sidebar ads or banner ads
- Ignore phone numbers in promotional banners, sponsored search results, or paid placements
"""
    
    @abstractmethod
    def construct_search_url(self, business_data: Dict) -> str:
        """Construct search URL for the business."""
        pass
    
    @abstractmethod
    def get_extraction_instruction(self, business_data: Dict) -> str:
        """Get LLM extraction instruction for this source."""
        pass
    
    def extract_phone_number(self, result) -> Optional[PhoneExtractionResult]:
        """Extract phone number from LLM extraction result."""
        try:
            extracted_content = result.extracted_content if hasattr(result, 'extracted_content') else result.get('extracted_content')
            if not extracted_content:
                return None
            
            # Parse JSON if it's a string
            if isinstance(extracted_content, str):
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
    
    async def scrape(self, business_data: Dict, crawler=None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Scrape phone number for a business with retry logic.
        
        Args:
            business_data: Business data dictionary
            crawler: Optional crawler instance (uses pool if None)
            
        Returns:
            Tuple of (phone_number, confidence, source_url) or (None, None, None) if not found
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
                
                # Use provided crawler or get from pool
                if crawler is None:
                    crawler = await get_crawler(browser_config)
                
                result = await crawler.arun(url=url, config=crawler_config)
                
                if result.success:
                    extraction_result = self.extract_phone_number(result)
                    if extraction_result and extraction_result.phone_number:
                        # Validate that phone is not from ads
                        page_content = ""
                        if hasattr(result, 'markdown') and result.markdown:
                            page_content = result.markdown.raw_markdown if hasattr(result.markdown, 'raw_markdown') else str(result.markdown)
                        elif hasattr(result, 'cleaned_html'):
                            page_content = str(result.cleaned_html)
                        elif hasattr(result, 'html'):
                            page_content = str(result.html)
                        
                        # Validate phone is not from ads
                        is_valid, validation_reason = await self.validate_phone_not_from_ads(
                            extraction_result.phone_number,
                            business_data,
                            page_content,
                            crawler
                        )
                        
                        if is_valid:
                            return extraction_result.phone_number, extraction_result.confidence, url
                        else:
                            self.logger.info(f"Phone {extraction_result.phone_number} rejected: {validation_reason}")
                            # Continue to next attempt or source
                            continue
                
                # If extraction failed, try direct text extraction
                if hasattr(result, 'markdown') and result.markdown:
                    markdown_text = result.markdown.raw_markdown if hasattr(result.markdown, 'raw_markdown') else str(result.markdown)
                    phone = self.phone_validator.extract_phone_from_text(markdown_text)
                    if phone:
                        # Validate extracted phone
                        page_content = markdown_text
                        is_valid, validation_reason = await self.validate_phone_not_from_ads(
                            phone,
                            business_data,
                            page_content,
                            crawler
                        )
                        
                        if is_valid:
                            return phone, "low", url
                        else:
                            self.logger.info(f"Phone {phone} rejected: {validation_reason}")
                            continue
            
            except Exception as e:
                error_msg = str(e)
                self.logger.debug(f"Attempt {attempt + 1}/{self.max_attempts} failed for {self.source_name}: {error_msg}")
                
                if attempt == self.max_attempts - 1:
                    self.logger.error(f"All attempts failed for {self.source_name}: {error_msg}")
                    return None, None, None
        
        return None, None, None
    
    async def validate_phone_not_from_ads(
        self, 
        phone_number: str, 
        business_data: Dict, 
        page_content: str,
        crawler=None
    ) -> Tuple[bool, str]:
        """
        Use LLM to validate that a phone number is from the actual business, not ads.
        
        Args:
            phone_number: The extracted phone number to validate
            business_data: Original business data
            page_content: The page content/markdown where phone was found
            crawler: Optional crawler instance
        
        Returns:
            Tuple of (is_valid, reason) where is_valid is True if phone is legitimate
        """
        # Check if ad filtering is enabled
        scraper_config = self.app_config.get('swiss_phone_scraper', {})
        ad_filtering_config = scraper_config.get('ad_filtering', {})
        if not ad_filtering_config.get('enabled', True):
            return True, "Ad filtering disabled"
        
        try:
            business_name = self._get_primary_name(business_data)
            street = business_data.get('STREET', '').strip()
            city = business_data.get('MAIL_CITY', '').strip()
            
            # Create validation instruction
            validation_instruction = f"""Analyze the following page content and determine if the phone number "{phone_number}" belongs to the actual business "{business_name}" or if it comes from an advertisement, sponsored content, or promotional section.

Business details:
- Name: {business_name}
- Address: {street}, {city} (if available)

Page content (first 5000 characters):
{page_content[:5000]}

Your task:
1. Check if the phone number appears in an advertisement, sponsored listing, or promotional section
2. Look for indicators like "Ad", "Sponsored", "Advertisement", "Promoted", "Paid", "Banner" labels near the phone number
3. Determine if the phone number is in the main search results or in a sidebar/banner ad
4. Check if the phone number is associated with the actual business listing or with an advertiser

Return:
- is_legitimate: true if the phone belongs to the actual business, false if it's from ads
- reason: Brief explanation of your decision
- confidence: high/medium/low based on how certain you are"""
            
            # Use LLM API directly for validation (simpler than using crawler)
            # We'll create a simple text-based validation using the LLM
            from crawl4ai import AsyncWebCrawler
            
            # Create a minimal HTML page with the content
            html_content = f"""<html><body><pre>{page_content[:8000]}</pre></body></html>"""
            
            # Use a simple approach: create a temporary file or use data URL
            # For now, we'll use the LLM extraction strategy with a minimal HTML page
            browser_config = BrowserConfig(headless=True)
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=1,
                page_timeout=30000,
                extraction_strategy=LLMExtractionStrategy(
                    llm_config=self.llm_config,
                    schema=PhoneValidationResult.model_json_schema(),
                    extraction_type="schema",
                    instruction=validation_instruction,
                    force_json_response=True,
                    verbose=False,
                ),
            )
            
            # Use provided crawler or get from pool
            if crawler is None:
                crawler = await get_crawler(browser_config)
            
            # Create a temporary HTML file approach - actually, let's use a simpler method
            # We'll just analyze the content directly via LLM call
            # For now, use a data URI approach
            import urllib.parse
            encoded_content = urllib.parse.quote(html_content)
            data_url = f"data:text/html;charset=utf-8,{encoded_content}"
            
            validation_result = await crawler.arun(url=data_url, config=crawler_config)
            
            if validation_result.success:
                extracted = validation_result.extracted_content
                if isinstance(extracted, str):
                    try:
                        extracted = json.loads(extracted)
                    except json.JSONDecodeError:
                        # Fallback: assume legitimate if we can't parse
                        self.logger.warning(f"Could not parse validation result, assuming legitimate")
                        return True, "Validation result unparseable, defaulting to legitimate"
                
                if isinstance(extracted, list) and extracted:
                    extracted = extracted[0]
                
                is_legitimate = extracted.get('is_legitimate', True)
                reason = extracted.get('reason', 'No reason provided')
                
                if not is_legitimate:
                    self.logger.info(f"Phone {phone_number} rejected as ad: {reason}")
                
                return is_legitimate, reason
            else:
                # If validation fails, default to accepting (to avoid false negatives)
                self.logger.warning(f"Validation failed, defaulting to accepting phone number")
                return True, "Validation failed, defaulting to legitimate"
        
        except Exception as e:
            self.logger.error(f"Error validating phone for ads: {str(e)}", exc_info=True)
            # Default to accepting to avoid false negatives
            return True, f"Validation error: {str(e)}, defaulting to legitimate"
    
    def validate_address_match(self, found_address: Optional[str], 
                               business_data: Dict) -> bool:
        """Validate if found address matches business address (fuzzy match)."""
        if not found_address:
            return False
        
        # Get business address components
        street = business_data.get('STREET', '').lower()
        city = business_data.get('MAIL_CITY', '').lower()
        
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

