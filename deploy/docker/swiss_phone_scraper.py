"""Handler for Swiss phone scraper functionality."""

import json
import logging
import asyncio
import sys
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from difflib import SequenceMatcher
from redis import asyncio as aioredis

# Ensure the current directory is in the path for imports
_current_dir = Path(__file__).parent.absolute()
_current_dir_str = str(_current_dir)
if _current_dir_str not in sys.path:
    sys.path.insert(0, _current_dir_str)

from scrapers.local_ch import LocalChScraper
from scrapers.search_ch import SearchChScraper
from scrapers.tel_search_ch import TelSearchChScraper
from scrapers.google_search import GoogleSearchScraper
from scrapers.zefix_ch import ZefixChScraper
from utils import PhoneValidator
from utils import TaskStatus
from crawler_pool import get_crawler
from crawl4ai import BrowserConfig

logger = logging.getLogger(__name__)


async def process_swiss_phone_scraper(
    redis: aioredis.Redis,
    config: dict,
    task_id: str,
    businesses: List[Dict],
    sources: Optional[List[str]] = None,
    override_config: Optional[Dict] = None
) -> None:
    """Process Swiss phone scraper job in background."""
    try:
        scraper_config = config.get('swiss_phone_scraper', {})
        if override_config:
            scraper_config.update(override_config)
        
        # Initialize scrapers
        available_scrapers = {
            'local_ch': LocalChScraper,
            'search_ch': SearchChScraper,
            'tel_search_ch': TelSearchChScraper,
            'google_search': GoogleSearchScraper,
            'zefix_ch': ZefixChScraper,
        }
        
        # Filter scrapers based on sources parameter
        if sources:
            scrapers = [
                scraper_class(
                    scraper_config.get('sources', {}).get(source_name, {}),
                    config
                )
                for source_name, scraper_class in available_scrapers.items()
                if source_name in sources and scraper_config.get('sources', {}).get(source_name, {}).get('enabled', True)
            ]
        else:
            # Use all enabled scrapers
            scrapers = [
                scraper_class(
                    scraper_config.get('sources', {}).get(source_name, {}),
                    config
                )
                for source_name, scraper_class in available_scrapers.items()
                if scraper_config.get('sources', {}).get(source_name, {}).get('enabled', True)
            ]
        
        if not scrapers:
            await redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.FAILED,
                "error": "No enabled scrapers found"
            })
            return
        
        phone_validator = PhoneValidator()
        results = []
        stats = {
            'total': len(businesses),
            'found': 0,
            'not_found': 0,
            'by_source': {},
            'by_confidence': {'high': 0, 'medium': 0, 'low': 0},
        }
        
        # Initialize source stats
        for scraper in scrapers:
            stats['by_source'][scraper.source_name] = {'found': 0, 'attempted': 0}
        
        # Get a shared crawler from pool
        browser_config = BrowserConfig(headless=True)
        crawler = await get_crawler(browser_config)
        
        try:
            # Process each business
            for idx, business in enumerate(businesses):
                business_name = business.get('COMPANY_NAME0', '').strip()
                if not business_name:
                    firstname = business.get('FIRSTNAME', '').strip()
                    lastname = business.get('LASTNAME', '').strip()
                    business_name = f"{firstname} {lastname}".strip()
                
                logger.info(f"Processing {idx + 1}/{len(businesses)}: {business_name}")
                
                phone = None
                source = None
                confidence = None
                source_url = None
                
                # Try each source in priority order
                for scraper in scrapers:
                    stats['by_source'][scraper.source_name]['attempted'] += 1
                    
                    try:
                        found_phone, found_confidence, found_url = await scraper.scrape(business, crawler=crawler)
                        
                        if found_phone:
                            # Validate phone
                            is_valid, normalized = phone_validator.validate_and_normalize(found_phone)
                            if is_valid:
                                phone = normalized
                                source = scraper.source_name
                                confidence = found_confidence or 'medium'
                                source_url = found_url or ''
                                
                                # Calculate confidence based on matching
                                confidence = calculate_confidence(business, None, None, source)
                                
                                stats['by_source'][scraper.source_name]['found'] += 1
                                logger.info(f"Found phone for {business_name}: {phone} (source: {source}, url: {source_url})")
                                break  # Found phone, stop trying other sources
                            else:
                                logger.warning(f"Invalid phone format from {scraper.source_name}: {found_phone}")
                    
                    except Exception as e:
                        logger.error(f"Error scraping {scraper.source_name} for {business_name}: {str(e)}", exc_info=True)
                
                # Update statistics
                if phone:
                    stats['found'] += 1
                    stats['by_confidence'][confidence] = stats['by_confidence'].get(confidence, 0) + 1
                else:
                    stats['not_found'] += 1
                
                # Add result
                result = business.copy()
                result['phone_number'] = phone or ''
                result['source'] = source or ''
                result['source_url'] = source_url or ''
                result['confidence_score'] = confidence or ''
                result['extraction_date'] = datetime.now().isoformat()
                results.append(result)
                
                # Update progress in Redis
                progress = {
                    'processed': idx + 1,
                    'total': len(businesses),
                    'found': stats['found'],
                    'not_found': stats['not_found']
                }
                await redis.hset(f"task:{task_id}", mapping={
                    "progress": json.dumps(progress)
                })
        
        finally:
            # Don't close crawler - it's from the pool and will be managed by janitor
            pass
        
        # Save final results atomically
        final_result = {
            'results': results,
            'statistics': stats,
            'processed_at': datetime.now().isoformat()
        }
        
        # Save result and status in a single atomic operation
        try:
            result_json = json.dumps(final_result)
            result_size = len(result_json)
            logger.info(f"Saving result for task {task_id}: {len(results)} results, JSON size: {result_size} bytes")
            
            # Save in a single operation
            await redis.hset(f"task:{task_id}", mapping={
                "result": result_json,
                "status": TaskStatus.COMPLETED
            })
            
            # Verify the result was saved
            saved_result = await redis.hget(f"task:{task_id}", "result")
            if not saved_result:
                logger.error(f"Failed to save result for task {task_id} - result is None")
                await redis.hset(f"task:{task_id}", mapping={
                    "status": TaskStatus.FAILED,
                    "error": "Failed to save results to Redis"
                })
            else:
                saved_size = len(saved_result) if isinstance(saved_result, (str, bytes)) else 0
                logger.info(f"Result saved successfully for task {task_id}: {saved_size} bytes saved")
                
                # Double-check the status was also saved
                saved_status = await redis.hget(f"task:{task_id}", "status")
                if saved_status != TaskStatus.COMPLETED:
                    logger.warning(f"Status mismatch for task {task_id}: expected {TaskStatus.COMPLETED}, got {saved_status}")
                    # Fix the status
                    await redis.hset(f"task:{task_id}", "status", TaskStatus.COMPLETED)
        except Exception as e:
            logger.error(f"Error saving result for task {task_id}: {str(e)}", exc_info=True)
            await redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.FAILED,
                "error": f"Failed to save results: {str(e)}"
            })
            raise
        
        logger.info(f"Swiss phone scraper job {task_id} completed: {stats['found']}/{stats['total']} found")
    
    except Exception as e:
        logger.error(f"Swiss phone scraper error: {str(e)}", exc_info=True)
        await redis.hset(f"task:{task_id}", mapping={
            "status": TaskStatus.FAILED,
            "error": str(e)
        })


def calculate_confidence(business_data: Dict, found_name: Optional[str], 
                       found_address: Optional[str], source: str) -> str:
    """Calculate confidence score based on matching."""
    business_name = business_data.get('COMPANY_NAME0', '').strip().lower()
    street = business_data.get('STREET', '').strip().lower()
    city = business_data.get('MAIL_CITY', '').strip().lower()
    
    name_match = False
    address_match = False
    
    if found_name and business_name:
        # Fuzzy name matching
        similarity = SequenceMatcher(None, business_name, found_name.lower()).ratio()
        name_match = similarity > 0.8
    
    if found_address:
        found_lower = found_address.lower()
        # Check city match
        if city and city in found_lower:
            # Check street match
            if street:
                street_words = [w for w in street.split() if len(w) > 3]
                if street_words:
                    address_match = any(word in found_lower for word in street_words)
                else:
                    address_match = street in found_lower
            else:
                address_match = True
    
    # Determine confidence
    if name_match and address_match:
        return 'high'
    elif name_match:
        return 'medium'
    else:
        return 'low'

