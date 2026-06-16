"""
BotDetect captcha solver for fao.ge.ch using crawl4ai + CapSolver.

Flow:
  1. Load page with session
  2. Extract captcha image as base64 via JS canvas
  3. Send to CapSolver ImageToTextTask with the ``number`` module (FAO captcha is digits only)
  4. Fill the input and submit via the Valider button click; wait_for the captcha image to disappear
  5. Return HTML; success is false if captcha markup or "code saisi est incorrect" remains.
  6. Optional follow URLs only after primary captcha cleared; optional LLM extraction

The CapSolver answer is used verbatim (no stripping / no uppercasing).

Env: CAPSOLVER_API_KEY (required), PROXY_URL / PROXY_USERNAME / PROXY_PASSWORD (optional).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse, urlunparse

import capsolver
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    GeolocationConfig,
    LLMConfig,
    LXMLWebScrapingStrategy,
    ProxyConfig,
)
from crawl4ai.extraction_strategy import LLMExtractionStrategy

from utils import (
    get_llm_api_key,
    get_llm_base_url,
    get_llm_temperature,
    validate_llm_provider,
)

logger = logging.getLogger(__name__)

capsolver.api_key = os.getenv("CAPSOLVER_API_KEY", "")

_FAO_LOCALE = "fr-CH"
_FAO_TIMEZONE = "Europe/Zurich"
_FAO_GEO = GeolocationConfig(latitude=46.2044, longitude=6.1432, accuracy=10.0)

_FAO_WAIT_UNTIL = "load"
_FAO_PAGE_TIMEOUT_MS = 90000

_DEFAULT_POST_CAPTCHA_WAIT_FOR = (
    "js:() => !document.getElementById('FAOCaptcha_CaptchaImage')"
)


def _browser_config() -> BrowserConfig:
    proxy_url = os.getenv("PROXY_URL")

    return BrowserConfig(
        headless=True,
        verbose=True,
        enable_stealth=True,
        use_persistent_context=False,
        headers={
            "Accept-Language": "fr-CH, fr;q=0.9, de;q=0.8, en;q=0.7",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
        },
        proxy_config=ProxyConfig(
            server=proxy_url,
            username=os.getenv("PROXY_USERNAME"),
            password=os.getenv("PROXY_PASSWORD"),
        )
        if proxy_url
        else None,
    )


JS_EXTRACT_CAPTCHA_IMAGE = """
    const img = document.getElementById('FAOCaptcha_CaptchaImage');
    if (!img) return JSON.stringify({error: 'captcha image not found'});

    if (!img.complete) {
        await new Promise(resolve => {
            img.onload = resolve;
            setTimeout(resolve, 5000);
        });
    }

    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const base64 = canvas.toDataURL('image/png').split(',')[1];

    return JSON.stringify({base64: base64});
"""


def js_fill_and_submit(answer: str) -> str:
    value_js = json.dumps(answer)
    return f"""
        const input = document.getElementById('captchaCode');
        if (input) {{
            input.removeAttribute('disabled');
            input.value = {value_js};
            input.dispatchEvent(new Event('input', {{bubbles: true}}));
            input.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
        const btn = document.getElementById('fao_captcha_submit');
        if (btn) {{ btn.click(); }}
    """


def _unwrap_js_payload(js_execution_result) -> dict | None:
    if not js_execution_result:
        return None

    payload = js_execution_result
    if isinstance(payload, dict) and "results" in payload:
        results = payload.get("results") or []
        payload = results[0] if results else None
    elif isinstance(payload, list) and len(payload) > 0:
        payload = payload[0]

    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, dict):
        return payload
    return None


def _error(url: str, msg: str) -> dict:
    return {
        "url": url,
        "html": None,
        "markdown": None,
        "success": False,
        "captcha_answer": None,
        "error": msg,
        "pages": [],
    }


def _enrich_nav_error(msg: str) -> str:
    if "ERR_HTTP_RESPONSE_CODE_FAILURE" in msg or "net::ERR_" in msg:
        return (
            f"{msg} "
            "This usually means the site returned an error status (for example 403) "
            "to automated or datacenter traffic. Try: (1) set PROXY_URL (+ user/pass) "
            "to a residential or Swiss exit IP, (2) crawl the exact page URL you need "
            "instead of only the site root if the homepage blocks bots."
        )
    return msg


def _resolve_follow_url(base: str, follow: str) -> str:
    follow = (follow or "").strip()
    if not follow:
        return base
    if follow.startswith(("http://", "https://")):
        return follow
    if follow.startswith("?"):
        p = urlparse(base)
        q = follow[1:].lstrip("?")
        path = p.path or "/"
        return urlunparse((p.scheme, p.netloc, path, p.params, q, ""))
    return urljoin(base, follow)


def _parse_extracted_content(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _html_indicates_captcha_failed(html: Optional[str]) -> bool:
    if not html:
        return False
    if "FAOCaptcha_CaptchaImage" in html:
        return True
    if "code saisi est incorrect" in html.lower():
        return True
    return False


def _crawl_result_to_page(url_hint: str, result) -> dict:
    md = result.markdown
    raw_markdown = getattr(md, "raw_markdown", None) if md is not None else None
    page_url = result.redirected_url or result.url or url_hint
    err = None if result.success else (result.error_message or "crawl failed")
    return {
        "url": page_url,
        "html": result.html,
        "markdown": raw_markdown,
        "success": result.success,
        "error": err,
        "extracted_content": _parse_extracted_content(result.extracted_content),
    }


def _shared_run_kwargs(session_id: str, extraction: Optional[LLMExtractionStrategy]) -> dict:
    kw: dict = dict(
        cache_mode=CacheMode.BYPASS,
        session_id=session_id,
        locale=_FAO_LOCALE,
        timezone_id=_FAO_TIMEZONE,
        geolocation=_FAO_GEO,
        wait_until=_FAO_WAIT_UNTIL,
        page_timeout=_FAO_PAGE_TIMEOUT_MS,
    )
    if extraction is not None:
        kw["extraction_strategy"] = extraction
        kw["scraping_strategy"] = LXMLWebScrapingStrategy()
    return kw


def _build_llm_extraction(
    app_config: Dict[str, Any],
    *,
    llm_instruction: str,
    llm_schema: Any,
    llm_provider: Optional[str],
    llm_temperature: Optional[float],
) -> LLMExtractionStrategy:
    ok, err_msg = validate_llm_provider(app_config, llm_provider)
    if not ok:
        raise ValueError(err_msg)
    schema_obj = None
    if llm_schema is not None:
        if isinstance(llm_schema, str):
            try:
                schema_obj = json.loads(llm_schema)
            except json.JSONDecodeError as e:
                raise ValueError(f"llm_schema is not valid JSON: {e}") from e
        else:
            schema_obj = llm_schema
    prov = llm_provider or app_config.get("llm", {}).get("provider")
    return LLMExtractionStrategy(
        llm_config=LLMConfig(
            provider=prov,
            api_token=get_llm_api_key(app_config, llm_provider),
            temperature=(
                llm_temperature
                if llm_temperature is not None
                else get_llm_temperature(app_config, llm_provider)
            ),
            base_url=get_llm_base_url(app_config, llm_provider),
        ),
        instruction=llm_instruction,
        schema=schema_obj,
    )


async def crawl_with_botdetect(
    url: str,
    *,
    app_config: Optional[Dict[str, Any]] = None,
    follow_urls: Optional[List[str]] = None,
    wait_for: Optional[str] = None,
    llm_extract: bool = False,
    llm_instruction: Optional[str] = None,
    llm_schema: Any = None,
    llm_provider: Optional[str] = None,
    llm_temperature: Optional[float] = None,
) -> dict:
    if not capsolver.api_key:
        return _error(url, "CAPSOLVER_API_KEY is not set")

    extraction: Optional[LLMExtractionStrategy] = None
    if llm_extract:
        if not llm_instruction:
            return _error(url, "llm_instruction is required when llm_extract is true")
        if app_config is None:
            return _error(url, "app_config is required for LLM extraction")
        try:
            extraction = _build_llm_extraction(
                app_config,
                llm_instruction=llm_instruction,
                llm_schema=llm_schema,
                llm_provider=llm_provider,
                llm_temperature=llm_temperature,
            )
        except ValueError as e:
            return _error(url, str(e))

    wait_spec = (wait_for or "").strip() or _DEFAULT_POST_CAPTCHA_WAIT_FOR
    resolved_follow: List[str] = []
    for raw in follow_urls or []:
        if isinstance(raw, str) and raw.strip():
            resolved_follow.append(_resolve_follow_url(url, raw.strip()))

    browser_cfg = _browser_config()
    session_id = f"fao_captcha_{uuid.uuid4().hex[:16]}"

    try:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:

            result = await crawler.arun(
                url=url,
                cache_mode=CacheMode.BYPASS,
                config=CrawlerRunConfig(
                    magic=True,
                    wait_until=_FAO_WAIT_UNTIL,
                    page_timeout=_FAO_PAGE_TIMEOUT_MS,
                    session_id=session_id,
                    locale=_FAO_LOCALE,
                    timezone_id=_FAO_TIMEZONE,
                    geolocation=_FAO_GEO,
                ),
            )

            if not result.success:
                return _error(
                    url,
                    _enrich_nav_error(
                        f"Failed to load page: {result.error_message}"
                    ),
                )

            extract_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                session_id=session_id,
                js_code=JS_EXTRACT_CAPTCHA_IMAGE,
                js_only=True,
                page_timeout=30000,
                locale=_FAO_LOCALE,
                timezone_id=_FAO_TIMEZONE,
                geolocation=_FAO_GEO,
            )
            extract_result = await crawler.arun(url=url, config=extract_config)

            js_output = _unwrap_js_payload(extract_result.js_execution_result)
            if not js_output or "base64" not in js_output:
                return _error(
                    url,
                    f"Failed to extract captcha image: {js_output}",
                )

            captcha_b64 = js_output["base64"]

            capsolver_task: Dict[str, Any] = {
                "type": "ImageToTextTask",
                "body": captcha_b64,
                "images": [captcha_b64],
                "websiteURL": url,
                "module": "number",
            }
            solution = capsolver.solve(capsolver_task)

            answer = (solution.get("text") or "").strip()
            if not answer:
                answers = solution.get("answers") or []
                answer = (answers[0] if answers else "").strip()
            if not answer:
                return _error(url, "CapSolver returned empty answer")

            logger.info("[captcha] CapSolver OCR result: %s", answer)

            submit_kw = _shared_run_kwargs(session_id, extraction)

            submit_config = CrawlerRunConfig(
                js_code_before_wait=js_fill_and_submit(answer),
                wait_for=wait_spec,
                wait_for_timeout=min(_FAO_PAGE_TIMEOUT_MS, 120000),
                js_only=True,
                **submit_kw,
            )
            final_result = await crawler.arun(url=url, config=submit_config)

            primary_page = _crawl_result_to_page(url, final_result)
            top_err = primary_page.get("error")
            primary_html = primary_page.get("html")
            captcha_still = _html_indicates_captcha_failed(primary_html)
            logical_success = bool(
                primary_page.get("success") and not captcha_still and not top_err
            )
            if captcha_still and not top_err:
                top_err = (
                    "Captcha not cleared: page still shows BotDetect or "
                    "'code saisi est incorrect'. Check CAPSOLVER / image quality or retry."
                )

            pages: List[dict] = [primary_page]
            if not (captcha_still or top_err):
                follow_kw = _shared_run_kwargs(session_id, extraction)
                for next_url in resolved_follow:
                    follow_result = await crawler.arun(
                        url=next_url,
                        config=CrawlerRunConfig(
                            magic=True,
                            js_only=False,
                            **follow_kw,
                        ),
                    )
                    pages.append(_crawl_result_to_page(next_url, follow_result))

            primary = pages[0]

            return {
                "url": url,
                "html": primary.get("html"),
                "markdown": primary.get("markdown"),
                "success": logical_success,
                "captcha_answer": answer,
                "error": top_err,
                "pages": pages,
            }

    except Exception as e:
        logger.exception("crawl_with_botdetect failed for %s", url)
        return _error(url, _enrich_nav_error(str(e)))
