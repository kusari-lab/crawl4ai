#!/usr/bin/env python3
"""Simple script to test Swiss phone scraper API."""

import requests
import json
import csv
import time
import sys

API_BASE = "http://localhost:11235"

def test_scraper_api(csv_file, sources=None):
    """Test the scraper via API."""
    # Read CSV
    businesses = []
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        businesses = list(reader)
    
    # Limit to first 5 for testing
    businesses = businesses[:5]
    print(f"Testing with {len(businesses)} businesses...")
    
    # Start job
    payload = {
        "businesses": businesses,
        "sources": sources.split(',') if sources else None
    }
    
    print(f"POST {API_BASE}/swiss-phone-scraper/job")
    try:
        response = requests.post(f"{API_BASE}/swiss-phone-scraper/job", json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error: Failed to connect to API at {API_BASE}")
        print(f"  Make sure the Docker container is running and the API is accessible")
        print(f"  Error details: {e}")
        sys.exit(1)
    
    task_id = response.json()['task_id']
    print(f"Task ID: {task_id}")
    print(f"Status URL: {API_BASE}/swiss-phone-scraper/job/{task_id}\n")
    
    # Poll for completion
    print("Polling for results...")
    while True:
        try:
            response = requests.get(f"{API_BASE}/swiss-phone-scraper/job/{task_id}")
            response.raise_for_status()
            status = response.json()
        except requests.exceptions.RequestException as e:
            print(f"\nError polling status: {e}")
            break
        
        if status['status'] == 'completed':
            results = status['result']
            stats = results['statistics']
            print(f"\n✓ Completed!")
            print(f"  Found: {stats['found']}/{stats['total']}")
            print(f"  Success rate: {stats['found'] / stats['total'] * 100:.1f}%")
            
            # Print results
            print(f"\nResults:")
            for i, business in enumerate(results['results'], 1):
                name = business.get('COMPANY_NAME0', business.get('ADDRESS_LINE1', 'N/A'))
                phone = business.get('phone_number', 'Not found')
                source = business.get('source', 'N/A')
                confidence = business.get('confidence_score', 'N/A')
                status_icon = "✓" if phone and phone != 'Not found' else "✗"
                print(f"  {status_icon} {i}. {name[:40]:<40} | {phone:<20} | {source} ({confidence})")
            break
        elif status['status'] == 'failed':
            print(f"\n✗ Failed: {status.get('error', 'Unknown error')}")
            break
        else:
            progress_str = status.get('progress', '{}')
            if isinstance(progress_str, str):
                try:
                    progress = json.loads(progress_str)
                except:
                    progress = {}
            else:
                progress = progress_str
            
            processed = progress.get('processed', 0)
            total = progress.get('total', 0)
            found = progress.get('found', 0)
            if total > 0:
                percent = processed / total * 100
                print(f"  Progress: {processed}/{total} ({percent:.1f}%) | Found: {found}", end='\r')
            else:
                print(f"  Status: {status['status']}", end='\r')
            time.sleep(2)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_scraper_api.py <csv_file> [sources]")
        print("Example: python test_scraper_api.py business_list.csv local_ch,search_ch")
        print("\nNote: This script expects the API to be running at http://localhost:11235")
        sys.exit(1)
    
    sources = sys.argv[2] if len(sys.argv) > 2 else None
    test_scraper_api(sys.argv[1], sources)

