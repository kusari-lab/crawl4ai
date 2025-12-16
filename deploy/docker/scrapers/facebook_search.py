"""Link-only Facebook profile discovery via Google Search."""

from typing import Dict, Optional, Tuple, Any
from urllib.parse import quote_plus
import json
import re

from pydantic import BaseModel, Field
from crawl4ai import CrawlerRunConfig, BrowserConfig, CacheMode
from crawl4ai.extraction_strategy import LLMExtractionStrategy

from .base_scraper import BaseScraper
from crawler_pool import get_crawler


class SocialLinkResult(BaseModel):
    profile_url: Optional[str] = Field(None, description="Best matching Facebook profile/page URL for the business")
    confidence: str = Field("low", description="high/medium/low")
    notes: Optional[str] = Field(None, description="Notes about why this URL was chosen")


class FacebookSearchScraper(BaseScraper):
    """Find a Facebook page/profile URL for the business (no phone extraction)."""

    provides_phone = False

    def construct_search_url(self, business_data: Dict) -> str:
        q = self._build_query(business_data)
        return f"https://www.google.com/search?q={quote_plus(q)}"

    def get_extraction_instruction(self, business_data: Dict) -> str:
        q = self._build_query(business_data)
        name = (business_data.get("cleaned_name") or self._get_primary_name(business_data) or "").strip()
        city = (business_data.get("MAIL_CITY") or "").strip()
        addr = (business_data.get("standardized_address") or "").strip()

        return f"""You are looking at Google search results.

Task: find the best matching official Facebook URL for this business.

Business hints:
- Name: {name}
- City: {city}
- Address: {addr}

Search query used: \"{q}\"

Return JSON with:
- profile_url: a single Facebook page/profile URL (must contain facebook.com)
- confidence: high/medium/low
- notes: short reasoning

If you cannot find a clear match, return null for profile_url.

IMPORTANT:
- Prefer business pages (e.g. facebook.com/<page>) over share links.
- Do not return ad/sponsored URLs.
"""

    def _build_query(self, business_data: Dict) -> str:
        name = (business_data.get("cleaned_name") or self._get_primary_name(business_data) or "").strip()
        city = (business_data.get("MAIL_CITY") or "").strip()
        parts = [p for p in (f"site:facebook.com", name, city) if p]
        return " ".join(parts).strip()

    async def scrape(self, business_data: Dict, crawler=None) -> Tuple[None, None, Optional[str], Optional[Dict[str, Any]]]:
        url = self.construct_search_url(business_data)
        instruction = self.get_extraction_instruction(business_data)

        # Minimal rate limit (reuse BaseScraper timing config)
        # Keep retry count consistent with BaseScraper config.
        for attempt in range(self.max_attempts):
            try:
                if attempt > 0:
                    delay = self.base_delay * (2 ** attempt) if self.exponential_backoff else self.base_delay
                    import asyncio
                    await asyncio.sleep(delay)
                else:
                    import asyncio
                    await asyncio.sleep(self.delay_seconds)

                browser_config = BrowserConfig(headless=True)
                crawler_config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    word_count_threshold=1,
                    page_timeout=60000,
                    extraction_strategy=LLMExtractionStrategy(
                        llm_config=self.llm_config,
                        schema=SocialLinkResult.model_json_schema(),
                        extraction_type="schema",
                        instruction=instruction,
                        force_json_response=True,
                        verbose=False,
                    ),
                )

                if crawler is None:
                    crawler = await get_crawler(browser_config)

                result = await crawler.arun(url=url, config=crawler_config)
                if not result.success:
                    continue

                extracted = result.extracted_content
                if isinstance(extracted, str):
                    try:
                        extracted = json.loads(extracted)
                    except json.JSONDecodeError:
                        extracted = {}
                if isinstance(extracted, list) and extracted:
                    extracted = extracted[0]

                profile_url = (extracted.get("profile_url") or "").strip()
                profile_url = self._normalize_fb_url(profile_url)
                if profile_url:
                    meta = {"social": {"facebook_url": profile_url}}
                    return None, None, url, meta

            except Exception:
                if attempt == self.max_attempts - 1:
                    return None, None, url, None

        return None, None, url, None

    def _normalize_fb_url(self, url: str) -> str:
        if not url:
            return ""
        u = url.strip()
        if "facebook.com" not in u.lower():
            return ""
        # reject share/dialog/login-ish links
        bad = ("sharer.php", "/share", "/dialog/", "login", "l.php")
        if any(b in u.lower() for b in bad):
            return ""
        # basic sanity
        if not re.match(r"^https?://", u, flags=re.I):
            return ""
        return u


