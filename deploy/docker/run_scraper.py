#!/usr/bin/env python3
"""Standalone CLI script to run Swiss phone scraper directly."""

import asyncio
import json
import csv
import sys
from pathlib import Path
from typing import List, Dict, Optional

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from swiss_phone_scraper import process_swiss_phone_scraper
from utils import load_config, TaskStatus
from redis import asyncio as aioredis

# Optional preprocessing before sending into the job (also happens inside the job)
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
        return business


async def main():
    """Run scraper directly from command line."""
    if len(sys.argv) < 2:
        print("Usage: python run_scraper.py <csv_file> [sources] [output_file]")
        print("  csv_file: Path to CSV file with business data")
        print("  sources: Comma-separated list (e.g., local_ch,search_ch) [optional]")
        print("  output_file: Output CSV file path [optional, defaults to input_with_phones.csv]")
        print("\nExample:")
        print("  python run_scraper.py business_list.csv local_ch,search_ch")
        sys.exit(1)
    
    csv_file = Path(sys.argv[1])
    if not csv_file.exists():
        print(f"Error: CSV file not found: {csv_file}")
        sys.exit(1)
    
    sources = sys.argv[2].split(',') if len(sys.argv) > 2 else None
    output_file = sys.argv[3] if len(sys.argv) > 3 else str(csv_file).replace('.csv', '_with_phones.csv')
    
    # Load config
    config = load_config()
    
    # Connect to Redis
    redis_config = config.get('redis', {})
    redis = aioredis.Redis(
        host=redis_config.get('host', 'localhost'),
        port=redis_config.get('port', 6379),
        db=redis_config.get('db', 0),
        password=redis_config.get('password', ''),
        decode_responses=False
    )
    
    # Parse CSV
    print(f"Loading businesses from {csv_file}...")
    businesses = []
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        businesses = list(reader)
    
    if not businesses:
        print("Error: No businesses found in CSV file")
        sys.exit(1)
    
    print(f"Loaded {len(businesses)} businesses")
    if sources:
        print(f"Using sources: {', '.join(sources)}")
    else:
        print("Using all available sources")

    # Preprocess + prioritize (so early progress yields higher match rates)
    businesses = [preprocess_business_row(b) for b in (businesses or [])]
    businesses.sort(key=lambda b: int(b.get('search_priority', 2)) if str(b.get('search_priority', '')).isdigit() else 2)
    
    # Generate task ID
    import uuid
    from datetime import datetime
    task_id = f"direct_{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:8]}"
    
    # Run scraper
    print(f"\nStarting scraper (task_id: {task_id})...")
    print("This may take a while depending on the number of businesses...\n")
    
    try:
        await process_swiss_phone_scraper(
            redis=redis,
            config=config,
            task_id=task_id,
            businesses=businesses,
            sources=sources,
            override_config=None
        )
        
        # Get results
        result_data = await redis.hget(f"task:{task_id}", "result")
        if not result_data:
            print("Error: No results found")
            sys.exit(1)
        
        result = json.loads(result_data)
        results = result.get('results', [])
        stats = result.get('statistics', {})
        
        # Print statistics
        print("\n" + "="*80)
        print("RESULTS")
        print("="*80)
        print(f"\nStatistics:")
        print(f"  Total businesses: {stats.get('total', 0)}")
        print(f"  Phone numbers found: {stats.get('found', 0)}")
        print(f"  Not found: {stats.get('not_found', 0)}")
        total = stats.get('total', 1)
        found = stats.get('found', 0)
        print(f"  Success rate: {found / total * 100:.1f}%")
        
        # Save to CSV
        if results:
            print(f"\nSaving results to {output_file}...")
            fieldnames = list(results[0].keys())
            with open(output_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(results)
            print(f"✓ Results saved to: {output_file}")
        
        # Print sample results
        print(f"\nSample results (first 10):")
        for i, business in enumerate(results[:10], 1):
            name = business.get('COMPANY_NAME0', business.get('ADDRESS_LINE1', 'N/A'))
            phone = business.get('phone_number', 'Not found')
            source = business.get('source', 'N/A')
            confidence = business.get('confidence_score', 'N/A')
            status = "✓" if phone and phone != 'Not found' else "✗"
            print(f"  {status} {i}. {name[:40]:<40} | {phone:<20} | {source} ({confidence})")
        
        if len(results) > 10:
            print(f"  ... and {len(results) - 10} more results")
        
        print("\n✓ Scraping completed successfully!")
        
    except Exception as e:
        print(f"\n✗ Error during scraping: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())

