# Swiss Business Phone Number Retrieval System

An automated system for retrieving phone numbers for Swiss businesses using Crawl4AI with LLM extraction. Designed to process ~1500 businesses across multiple Swiss business directories with a target success rate of 50-70%.

## Features

- **Multi-Source Scraping**: Searches across 5 Swiss business directories:
  - local.ch - Main Swiss business directory
  - search.ch - Secondary directory
  - tel.search.ch - Phone-specific directory
  - Google Search - Business info panels
  - zefix.ch - Swiss commercial register

- **Intelligent Extraction**: Uses Crawl4AI with LLM extraction to intelligently find phone numbers
- **Smart Matching**: Tries business name first, falls back to owner name
- **Address Validation**: Validates results match business location
- **Confidence Scoring**: Assigns high/medium/low confidence scores
- **Resumable Processing**: Can stop/restart without losing progress
- **Progress Tracking**: Saves progress every 50 businesses
- **Comprehensive Logging**: Detailed logs of all attempts and results

## Project Structure

```
swiss_phone_scraper/
├── main.py                 # Main orchestrator script
├── config.yaml             # Configuration file
├── requirements.txt        # Python dependencies
├── README.md              # This file
├── scrapers/              # Source-specific scrapers
│   ├── __init__.py
│   ├── base_scraper.py    # Base class with common logic
│   ├── local_ch.py        # local.ch scraper
│   ├── search_ch.py       # search.ch scraper
│   ├── tel_search_ch.py   # tel.search.ch scraper
│   ├── google_search.py   # Google search scraper
│   └── zefix_ch.py        # zefix.ch scraper
├── utils/
│   ├── __init__.py
│   ├── phone_validator.py # Swiss phone format validation
│   ├── csv_handler.py     # CSV read/write operations
│   ├── progress_tracker.py # Resumable progress management
│   └── logger.py          # Structured logging
└── data/
    ├── business_list.csv  # Input CSV file
    ├── output/            # Output directory
    │   ├── enhanced_businesses.csv
    │   ├── detailed_log.txt
    │   └── summary_report.txt
    └── progress/          # Progress checkpoint files
        └── checkpoint.json
```

## Setup

### Prerequisites

- Python 3.8+
- Crawl4AI installed locally (already installed on your machine)
- OpenAI API key (or other LLM provider)

### Installation

1. **Install dependencies**:
   ```bash
   cd swiss_phone_scraper
   pip install -r requirements.txt
   ```

2. **Configure LLM API key**:
   
   The system will automatically try to load your API key from:
   - `.llm.env` file in the parent directory (if it exists)
   - `OPENAI_API_KEY` environment variable
   
   You can also set it in `config.yaml`:
   ```yaml
   llm:
     provider: "openai/gpt-4o-mini"
     api_token: "your-api-key-here"
   ```

3. **Prepare input data**:
   
   Place your `business_list.csv` file in `swiss_phone_scraper/data/` directory.
   
   The CSV should contain columns:
   - `COMPANY_NAME0` - Business name
   - `FIRSTNAME`, `LASTNAME` - Owner name (fallback)
   - `STREET` - Street address
   - `MAIL_CITY` - City
   - `MAIL_ZIP` - Postal code

## Configuration

Edit `config.yaml` to customize:

```yaml
llm:
  provider: "openai/gpt-4o-mini"  # LLM provider
  api_token: null  # Load from .llm.env or environment

sources:
  local_ch:
    enabled: true
    delay_seconds: 2  # Delay between requests
  search_ch:
    enabled: true
    delay_seconds: 2
  tel_search_ch:
    enabled: true
    delay_seconds: 2
  google_search:
    enabled: true
    delay_seconds: 3
  zefix_ch:
    enabled: true
    delay_seconds: 2

retry:
  max_attempts: 3  # Retry attempts per source
  base_delay: 1    # Base delay for retries
  exponential_backoff: true

progress:
  save_interval: 50  # Save progress every N businesses

validation:
  require_address_match: true
  min_confidence: "low"  # low/medium/high
```

## Usage

### Basic Usage

```bash
cd swiss_phone_scraper
python main.py
```

The system will:
1. Load businesses from `data/business_list.csv`
2. Check for existing progress (resume if interrupted)
3. Process each business through sources in priority order
4. Save progress every 50 businesses
5. Generate output files on completion

### Resuming Interrupted Runs

If the process is interrupted, simply run it again. The system will:
- Load the checkpoint from `data/progress/checkpoint.json`
- Skip already-processed businesses
- Continue from where it left off

## Output Files

### enhanced_businesses.csv

Enhanced CSV with original columns plus:
- `phone_number` - Found phone number (normalized format: +41 XX XXX XX XX)
- `source` - Source where phone was found
- `confidence_score` - Confidence level (high/medium/low)
- `extraction_date` - ISO timestamp of extraction

### detailed_log.txt

Comprehensive log file containing:
- All scraping attempts
- Success/failure status for each business
- Error messages and exceptions
- Timestamps for all operations

### summary_report.txt

Summary statistics including:
- Overall success rate
- Success rate by source
- Confidence breakdown
- Error list
- Processing statistics

## Phone Number Validation

The system validates Swiss phone numbers in these formats:
- International: `+41 XX XXX XX XX`
- Local: `0XX XXX XX XX`

All phone numbers are normalized to international format (`+41 XX XXX XX XX`).

## Confidence Scoring

- **High**: Exact business name match + address match
- **Medium**: Business name match, partial address match
- **Low**: Business name match only, or ambiguous results

## Error Handling

The system handles:
- Timeouts and network errors
- 404 errors and missing pages
- Rate limiting (with delays)
- Malformed CSV data
- Invalid phone number formats

All errors are logged but don't stop processing.

## Performance

- **Estimated Runtime**: 3-6 hours for 1500 businesses
- **Processing Speed**: ~4-8 businesses per minute (depends on delays)
- **Success Rate Target**: 50-70% (750-1050 phone numbers)

## Troubleshooting

### No phone numbers found

1. Check that your LLM API key is configured correctly
2. Verify the input CSV has correct column names
3. Check the detailed log for specific errors
4. Try increasing delays in `config.yaml` if rate limited

### Rate limiting

If you encounter rate limits:
- Increase `delay_seconds` in `config.yaml`
- Disable some sources temporarily
- Process in smaller batches

### Memory issues

For large datasets:
- Reduce `save_interval` to save more frequently
- Process in smaller batches by modifying the CSV

## License

This project is part of the crawl4ai ecosystem.

## Support

For issues or questions:
1. Check the detailed log file for specific errors
2. Review the summary report for statistics
3. Verify configuration in `config.yaml`

