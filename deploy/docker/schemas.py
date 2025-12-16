from typing import List, Optional, Dict
from enum import Enum
from pydantic import BaseModel, Field
from utils import FilterType


class CrawlRequest(BaseModel):
    urls: List[str] = Field(min_length=1, max_length=100)
    browser_config: Optional[Dict] = Field(default_factory=dict)
    crawler_config: Optional[Dict] = Field(default_factory=dict)

class MarkdownRequest(BaseModel):
    """Request body for the /md endpoint."""
    url: str                    = Field(...,  description="Absolute http/https URL to fetch")
    f:   FilterType             = Field(FilterType.FIT, description="Content‑filter strategy: fit, raw, bm25, or llm")
    q:   Optional[str] = Field(None,  description="Query string used by BM25/LLM filters")
    c:   Optional[str] = Field("0",   description="Cache‑bust / revision counter")
    provider: Optional[str] = Field(None, description="LLM provider override (e.g., 'anthropic/claude-3-opus')")


class RawCode(BaseModel):
    code: str

class HTMLRequest(BaseModel):
    url: str
    
class ScreenshotRequest(BaseModel):
    url: str
    screenshot_wait_for: Optional[float] = 2
    output_path: Optional[str] = None

class PDFRequest(BaseModel):
    url: str
    output_path: Optional[str] = None


class JSEndpointRequest(BaseModel):
    url: str
    scripts: List[str] = Field(
        ...,
        description="List of separated JavaScript snippets to execute"
    )


class SwissPhoneScraperRequest(BaseModel):
    businesses: Optional[List[Dict]] = Field(None, description="JSON array of business objects")
    sources: Optional[List[str]] = Field(None, description="Which sources to use (local_ch, search_ch, etc.)")
    config: Optional[Dict] = Field(None, description="Override configuration")
    source_priorities: Optional[Dict[str, int]] = Field(None, description="Map of source name to priority (1 = first, 2 = second, etc.)")
    enable_double_check: Optional[bool] = Field(False, description="Enable multi-source validation (check all sources and boost confidence for matches)")
    min_sources_for_high_confidence: Optional[int] = Field(2, description="Minimum number of sources needed for 'high' confidence")