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
from scrapers.facebook_search import FacebookSearchScraper
from scrapers.instagram_search import InstagramSearchScraper
from utils import PhoneValidator
from utils import TaskStatus
from crawler_pool import get_crawler
from crawl4ai import BrowserConfig

logger = logging.getLogger(__name__)

# Import business preprocessor (note: `deploy/docker/utils.py` shadows `deploy/docker/utils/`)
try:
    import importlib.util

    _pre_path = Path(__file__).parent / "utils" / "business_preprocessor.py"
    if _pre_path.exists():
        _spec = importlib.util.spec_from_file_location("business_preprocessor", _pre_path)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        preprocess_business_row = _mod.preprocess_business_row
    else:
        raise ImportError("business_preprocessor not found")
except (ImportError, Exception):
    def preprocess_business_row(business: Dict) -> Dict:  # type: ignore
        """Fallback: return business as-is."""
        return business


async def process_swiss_phone_scraper(
    redis: aioredis.Redis,
    config: dict,
    task_id: str,
    businesses: List[Dict],
    sources: Optional[List[str]] = None,
    override_config: Optional[Dict] = None,
    source_priorities: Optional[Dict[str, int]] = None,
    enable_double_check: bool = False,
    min_sources_for_high_confidence: int = 2
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
            'facebook_search': FacebookSearchScraper,
            'instagram_search': InstagramSearchScraper,
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
        
        # Sort scrapers by priority
        def get_priority(scraper):
            source_name = scraper.source_name
            # Use provided priorities if available, otherwise use config
            if source_priorities and source_name in source_priorities:
                return source_priorities[source_name]
            source_config = scraper_config.get('sources', {}).get(source_name, {})
            return source_config.get('priority', 999)  # Default to low priority if not set
        
        scrapers.sort(key=get_priority)
        
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
            # Pre-process and prioritize rows (HIGH quality first)
            businesses = [preprocess_business_row(b) for b in (businesses or [])]
            businesses.sort(key=lambda b: int(b.get('search_priority', 2)) if str(b.get('search_priority', '')).isdigit() else 2)

            # Split scrapers: phone-oriented vs social link discovery
            phone_scrapers = [s for s in scrapers if getattr(s, "provides_phone", True)]
            social_scrapers = [s for s in scrapers if not getattr(s, "provides_phone", True)]
            social_cfg = scraper_config.get('social_links', {}) if isinstance(scraper_config, dict) else {}
            social_enabled = social_cfg.get('enabled', True)
            social_run_even_if_phone_found = social_cfg.get('run_even_if_phone_found', True)

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
                found_business_name = None
                found_address = None
                found_status = None
                found_website = None
                match_method = None
                address_similarity = None
                sources_found_in = []
                social_links = {}  # facebook_url / instagram_url
                
                if enable_double_check:
                    # Multi-source validation: collect results from all sources
                    phone_results = {}  # Map of normalized phone -> list of result dicts
                    
                    for scraper in phone_scrapers:
                        stats['by_source'][scraper.source_name]['attempted'] += 1
                        
                        try:
                            scrape_result = await scraper.scrape(business, crawler=crawler)
                            found_phone, found_confidence, found_url = scrape_result[:3]
                            found_meta = scrape_result[3] if len(scrape_result) > 3 else None
                            
                            if found_phone:
                                # Validate phone
                                is_valid, normalized = phone_validator.validate_and_normalize(found_phone)
                                if is_valid:
                                    if normalized not in phone_results:
                                        phone_results[normalized] = []
                                    phone_results[normalized].append({
                                        'source': scraper.source_name,
                                        'confidence': found_confidence or 'medium',
                                        'url': found_url or '',
                                        'meta': found_meta or {}
                                    })
                                    stats['by_source'][scraper.source_name]['found'] += 1
                                    logger.info(f"Found phone {normalized} from {scraper.source_name} for {business_name}")
                                else:
                                    logger.warning(f"Invalid phone format from {scraper.source_name}: {found_phone}")
                            # Capture social links even in double-check mode if a phone scraper returns them
                            if isinstance(found_meta, dict) and isinstance(found_meta.get('social'), dict):
                                social_links.update(found_meta.get('social', {}))
                        
                        except Exception as e:
                            logger.error(f"Error scraping {scraper.source_name} for {business_name}: {str(e)}", exc_info=True)
                    
                    # Select best phone based on multi-source agreement
                    if phone_results:
                        best_phone = None
                        best_score = -1
                        best_sources = []
                        best_confidence = 'low'
                        best_url = ''
                        best_meta = {}
                        
                        for normalized_phone, results_list in phone_results.items():
                            source_count = len(results_list)
                            # Calculate aggregate confidence
                            confidences = [r['confidence'] for r in results_list]
                            base_confidence = max(confidences, key=lambda c: ['low', 'medium', 'high'].index(c))
                            
                            # Boost confidence based on source count
                            if source_count >= min_sources_for_high_confidence:
                                final_confidence = 'high'
                            elif source_count >= 2:
                                # Boost by one level
                                if base_confidence == 'low':
                                    final_confidence = 'medium'
                                else:
                                    final_confidence = 'high'
                            else:
                                final_confidence = base_confidence
                            
                            # Score: prioritize by source count, then confidence, then priority
                            confidence_score = ['low', 'medium', 'high'].index(final_confidence)
                            score = source_count * 100 + confidence_score
                            
                            if score > best_score:
                                best_score = score
                                best_phone = normalized_phone
                                best_sources = [r['source'] for r in results_list]
                                best_confidence = final_confidence
                                # Use URL from highest priority source
                                best_url = results_list[0]['url']
                                best_meta = results_list[0].get('meta') or {}
                        
                        if best_phone:
                            phone = best_phone
                            source = best_sources[0]  # Primary source (first in priority order)
                            sources_found_in = best_sources
                            confidence = best_confidence
                            source_url = best_url
                            if isinstance(best_meta, dict):
                                found_business_name = best_meta.get('business_name')
                                found_address = best_meta.get('address')
                                found_status = best_meta.get('status')
                                found_website = best_meta.get('website')
                                match_method = best_meta.get('method')
                                address_similarity = best_meta.get('address_similarity')
                            logger.info(f"Selected phone {phone} for {business_name} from {len(best_sources)} sources: {best_sources}")
                else:
                    # Original behavior: stop at first match
                    for scraper in phone_scrapers:
                        stats['by_source'][scraper.source_name]['attempted'] += 1
                        
                        try:
                            scrape_result = await scraper.scrape(business, crawler=crawler)
                            found_phone, found_confidence, found_url = scrape_result[:3]
                            found_meta = scrape_result[3] if len(scrape_result) > 3 else None
                            
                            if found_phone:
                                # Validate phone
                                is_valid, normalized = phone_validator.validate_and_normalize(found_phone)
                                if is_valid:
                                    phone = normalized
                                    source = scraper.source_name
                                    confidence = found_confidence or 'medium'
                                    source_url = found_url or ''
                                    sources_found_in = [scraper.source_name]
                                    
                                    # Calculate confidence based on matching
                                    if isinstance(found_meta, dict):
                                        found_business_name = found_meta.get('business_name')
                                        found_address = found_meta.get('address')
                                        found_status = found_meta.get('status')
                                        found_website = found_meta.get('website')
                                        match_method = found_meta.get('method')
                                        address_similarity = found_meta.get('address_similarity')
                                    confidence = calculate_confidence(business, found_business_name, found_address, source)
                                    
                                    stats['by_source'][scraper.source_name]['found'] += 1
                                    logger.info(f"Found phone for {business_name}: {phone} (source: {source}, url: {source_url})")
                                    break  # Found phone, stop trying other sources
                                else:
                                    logger.warning(f"Invalid phone format from {scraper.source_name}: {found_phone}")
                            # Capture social links even if phone wasn't found
                            if isinstance(found_meta, dict) and isinstance(found_meta.get('social'), dict):
                                social_links.update(found_meta.get('social', {}))
                        
                        except Exception as e:
                            logger.error(f"Error scraping {scraper.source_name} for {business_name}: {str(e)}", exc_info=True)

                # Social link discovery (link-only) optionally runs even if phone was found
                if social_enabled and social_scrapers and (social_run_even_if_phone_found or not phone):
                    for scraper in social_scrapers:
                        stats['by_source'][scraper.source_name]['attempted'] += 1
                        try:
                            scrape_result = await scraper.scrape(business, crawler=crawler)
                            found_phone, found_confidence, found_url = scrape_result[:3]
                            found_meta = scrape_result[3] if len(scrape_result) > 3 else None
                            if isinstance(found_meta, dict) and isinstance(found_meta.get('social'), dict):
                                social_links.update(found_meta.get('social', {}))
                                stats['by_source'][scraper.source_name]['found'] += 1
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
                result['found_business_name'] = found_business_name or ''
                result['found_address'] = found_address or ''
                result['found_status'] = found_status or ''
                result['found_website'] = found_website or ''
                result['match_method'] = match_method or ''
                result['address_similarity'] = address_similarity if address_similarity is not None else ''
                result['facebook_url'] = social_links.get('facebook_url', '') or ''
                result['instagram_url'] = social_links.get('instagram_url', '') or ''
                result['extraction_date'] = datetime.now().isoformat()
                if enable_double_check:
                    result['sources_found_in'] = sources_found_in
                    result['source_count'] = len(sources_found_in)
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

