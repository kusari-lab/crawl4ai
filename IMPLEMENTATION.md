# Crawler Service — Implementation Plan

> **Goal**: Modify your crawl4ai fork (branch `feature/res-custom`) to solve the
> BotDetect captcha on fao.ge.ch using CapSolver's ImageToTextTask, then
> deploy the Docker image to DigitalOcean App Platform.

---

## Architecture

```
Your Backend (cron job)
    └── POST /crawl-captcha → Your Fork's Docker Image (DO App Platform)
                                    ├── crawl4ai loads the page (stealth + proxy)
                                    ├── js_code extracts captcha image as base64
                                    ├── CapSolver OCRs the image (ImageToTextTask)
                                    ├── js_code types the answer + clicks submit
                                    └── returns the post-captcha page HTML/markdown
                                            └── Backend saves to DB
```

---

## BotDetect Captcha Flow (fao.ge.ch)

Unlike token-based captchas (reCAPTCHA, Turnstile), BotDetect shows a
distorted image with digits. The solving flow is:

```
1. Load page           → crawl4ai arun() with session
2. Extract image       → js_code converts <img> to base64 via canvas
3. OCR the image       → CapSolver ImageToTextTask (returns digit string)
4. Type answer + submit → js_code_before_wait fills #captchaCode + clicks submit;
                          wait_for until captcha image gone (then scrape)
5. Get result page     → crawl4ai returns post-captcha HTML/markdown; optional follow_urls
```

### fao.ge.ch selectors (from the page source)

```
Image:   #FAOCaptcha_CaptchaImage    (class BDC_CaptchaImage)
Input:   #captchaCode                (name fao_captcha[captchaCode])
Submit:  #fao_captcha_submit         (button "Valider")
Note:    digits only ("Veuillez introduire les chiffres")
```

---

## Repo: Your fork of crawl4ai

Branch: `custom-captcha`

### Files to change

| File | Change |
|---|---|
| `deploy/docker/requirements.txt` | Add `capsolver` (1 line) |
| `deploy/docker/captcha_crawl.py` | **New file** — BotDetect solving logic |
| `deploy/docker/server.py` | Add import + 1 new endpoint (~15 lines) |

---

## Step 1 — Create the branch

```bash
cd crawl4ai   # your fork
git checkout main
git pull origin main
git checkout -b custom-captcha
```

---

## Step 2 — Add capsolver to dependencies

**File**: `deploy/docker/requirements.txt`

Add at the end:

```
capsolver
```

---

## Step 3 — Create the captcha crawl module

**File to create**: `deploy/docker/captcha_crawl.py`

```python
"""
BotDetect captcha solver for fao.ge.ch using crawl4ai + CapSolver.

Flow:
  1. Load page with session
  2. Extract captcha image as base64 via JS canvas
  3. Send to CapSolver ImageToTextTask for OCR
  4. Type the answer into the input and submit
  5. Return the post-captcha page content
"""

import os
import capsolver
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    ProxyConfig,
)

capsolver.api_key = os.getenv("CAPSOLVER_API_KEY", "")


def _browser_config() -> BrowserConfig:
    """Browser config from env vars. Proxy and stealth are optional."""
    proxy_url = os.getenv("PROXY_URL")

    return BrowserConfig(
        headless=True,
        verbose=True,
        enable_stealth=True,
        use_persistent_context=True,
        proxy_config=ProxyConfig(
            server=proxy_url,
            username=os.getenv("PROXY_USERNAME"),
            password=os.getenv("PROXY_PASSWORD"),
        ) if proxy_url else None,
    )


# ── JavaScript snippets ──

JS_EXTRACT_CAPTCHA_IMAGE = """
(async () => {
    const img = document.getElementById('FAOCaptcha_CaptchaImage');
    if (!img) return JSON.stringify({error: 'captcha image not found'});

    // Wait for image to fully load
    if (!img.complete) {
        await new Promise(resolve => {
            img.onload = resolve;
            setTimeout(resolve, 5000);
        });
    }

    // Draw image to canvas and export as base64 PNG
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const base64 = canvas.toDataURL('image/png').split(',')[1];

    return JSON.stringify({base64: base64});
})()
"""


def js_fill_and_submit(answer: str) -> str:
    """JS to type the captcha answer and click Valider."""
    # Input is uppercase-only per the page CSS (text-transform: uppercase)
    return f"""
        const input = document.getElementById('captchaCode');
        if (input) {{
            input.value = '{answer.upper()}';
            input.dispatchEvent(new Event('input', {{bubbles: true}}));
            input.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
        const btn = document.getElementById('fao_captcha_submit');
        if (btn) {{ btn.click(); }}
    """


# ── Main crawl function ──

async def crawl_with_botdetect(url: str) -> dict:
    """
    Crawl a BotDetect-protected page on fao.ge.ch.

    1. Loads the page
    2. Extracts the captcha image as base64
    3. Sends to CapSolver for OCR
    4. Types the answer and submits the form
    5. Returns the post-captcha page content
    """
    browser_cfg = _browser_config()
    session_id = "fao_captcha_session"

    try:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:

            # ── Step 1: Load the captcha page ──
            result = await crawler.arun(
                url=url,
                cache_mode=CacheMode.BYPASS,
                config=CrawlerRunConfig(
                    magic=True,
                    wait_until="networkidle",
                    page_timeout=60000,
                    session_id=session_id,
                ),
            )

            if not result.success:
                return _error(url, f"Failed to load page: {result.error_message}")

            # ── Step 2: Extract captcha image as base64 ──
            extract_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                session_id=session_id,
                js_code=JS_EXTRACT_CAPTCHA_IMAGE,
                js_only=True,
                page_timeout=15000,
            )
            extract_result = await crawler.arun(url=url, config=extract_config)

            # Parse the base64 from js_execution_result
            import json
            js_output = extract_result.js_execution_result
            if isinstance(js_output, list) and len(js_output) > 0:
                js_output = js_output[0]
            if isinstance(js_output, str):
                js_output = json.loads(js_output)

            if not js_output or "base64" not in js_output:
                return _error(url, f"Failed to extract captcha image: {js_output}")

            captcha_b64 = js_output["base64"]

            # ── Step 3: Solve via CapSolver OCR ──
            solution = capsolver.solve({
                "type": "ImageToTextTask",
                "body": captcha_b64,
            })

            answer = solution.get("text", "")
            if not answer:
                return _error(url, "CapSolver returned empty answer")

            print(f"[captcha] CapSolver OCR result: {answer}")

            # ── Step 4: Type answer and submit ──
            submit_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                session_id=session_id,
                js_code=js_fill_and_submit(answer),
                js_only=True,
                wait_until="networkidle",
                page_timeout=30000,
            )
            final_result = await crawler.arun(url=url, config=submit_config)

            # ── Step 5: Return the post-captcha page ──
            return {
                "url": url,
                "html": final_result.html,
                "markdown": getattr(final_result.markdown, "raw_markdown", None)
                    if final_result.markdown else None,
                "success": final_result.success,
                "captcha_answer": answer,
                "error": None,
            }

    except Exception as e:
        return _error(url, str(e))


def _error(url: str, msg: str) -> dict:
    return {
        "url": url,
        "html": None,
        "markdown": None,
        "success": False,
        "captcha_answer": None,
        "error": msg,
    }
```

---

## Step 4 — Add the endpoint to the server

**File to edit**: `deploy/docker/server.py`

Add at the top with the other imports:

```python
from captcha_crawl import crawl_with_botdetect
```

Add this endpoint alongside the existing routes:

```python
@app.post("/crawl-captcha")
async def crawl_captcha_endpoint(request: Request):
    """
    Crawl a BotDetect-protected page.
    Body: {"url": "https://fao.ge.ch/..."}
    """
    data = await request.json()
    url = data.get("url")
    if not url:
        return JSONResponse(
            status_code=400,
            content={"error": "url is required"}
        )
    result = await crawl_with_botdetect(url)
    return JSONResponse(content=result)
```

---

## Step 5 — Set environment variables in DigitalOcean

```
CAPSOLVER_API_KEY=CAP-xxxxxxxxxxxxxxx

# Optional — only if the site blocks direct access
PROXY_URL=http://your-proxy-host:port
PROXY_USERNAME=your_user
PROXY_PASSWORD=your_pass
```

---

## Step 6 — Push and deploy

```bash
git add deploy/docker/requirements.txt
git add deploy/docker/captcha_crawl.py
git add deploy/docker/server.py
git commit -m "feat: add BotDetect captcha solver for fao.ge.ch"
git push origin custom-captcha
```

In DigitalOcean App Platform:
- Source: your fork
- Branch: `custom-captcha`
- Deploy (the existing Dockerfile handles everything)

---

## Step 7 — Call from your backend

### Crawl fao.ge.ch (with captcha solving)

```python
import httpx

CRAWLER_URL = "https://your-crawler.ondigitalocean.app"

async def crawl_fao():
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{CRAWLER_URL}/crawl-captcha",
            json={"url": "https://fao.ge.ch/the-page-you-need"}
        )
        data = response.json()
        if data["success"]:
            print(f"Captcha solved: {data['captcha_answer']}")
            save_to_db(data["html"])
        else:
            print(f"Failed: {data['error']}")
```

### Crawl other sites (no captcha, use standard endpoint)

```python
async def crawl_other():
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{CRAWLER_URL}/crawl",
            json={
                "urls": "https://other-site.com/page",
                "browser_config": {"headless": True, "enable_stealth": True},
                "crawler_config": {"magic": True}
            }
        )
```

### Cron schedule

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx

CRAWLER_URL = "https://your-crawler.ondigitalocean.app"

FAO_PAGES = [
    "https://fao.ge.ch/page-1",
    "https://fao.ge.ch/page-2",
]

async def scheduled_crawl():
    async with httpx.AsyncClient(timeout=120) as client:
        for url in FAO_PAGES:
            resp = await client.post(
                f"{CRAWLER_URL}/crawl-captcha",
                json={"url": url}
            )
            data = resp.json()
            if data["success"]:
                save_to_db(data)
            else:
                log_error(data["error"])

scheduler = AsyncIOScheduler()
scheduler.add_job(scheduled_crawl, "interval", hours=6)
scheduler.start()
```

---

## Step 8 — Keep fork in sync

```bash
# Sync main with upstream crawl4ai
git checkout main
git fetch upstream
git merge upstream/main
git push origin main

# Rebase your branch on top
git checkout custom-captcha
git rebase main
git push origin custom-captcha --force-with-lease
```

---

## Summary

### What crawl4ai handles (untouched)
- Proxy, stealth, magic mode, anti-bot detection
- Docker image, FastAPI server, Playwright, Redis
- Standard /crawl endpoint for non-captcha sites
- Session management across multiple arun() calls

### What you add (3 files)
- `capsolver` in requirements (1 line)
- `captcha_crawl.py` — extract image → OCR → type answer → submit
- `/crawl-captcha` endpoint in server.py (~15 lines)

### Key details for fao.ge.ch
- Captcha type: BotDetect (image with digits)
- CapSolver task: `ImageToTextTask` (sends base64, gets text back)
- Image selector: `#FAOCaptcha_CaptchaImage`
- Input selector: `#captchaCode`
- Submit selector: `#fao_captcha_submit`
- Digits only, uppercase (CSS text-transform applied)

### Potential issues to watch for
- **Captcha image CORS**: If the canvas `drawImage` fails due to CORS,
  the JS will need to fetch the image URL via `fetch()` + `blob` instead.
  The image src is relative: `captcha-handler?get=image&c=FAOCaptcha&t=...`
  which should be same-origin and work fine.
- **Session tokens**: The form has hidden fields (`BDC_VCID_FAOCaptcha`,
  `fao_captcha[_token]`). These are submitted automatically when you
  click the button via JS (the form handles it). No need to extract them.
- **Captcha reload**: If the first attempt fails, you may want to click
  `#FAOCaptcha_ReloadLink` to get a fresh captcha and retry.
- **Rate limiting**: CapSolver ImageToTextTask is fast (~1-2s) but if
  fao.ge.ch rate-limits, add a delay between crawls.
---

## Update — Post-captcha capture, multi-URL, LLM extraction

### Submit + wait pipeline

crawl4ai runs **`js_code` after `wait_for`**. Filling and submitting in **`js_code`**
only can race **`page.content()`** before navigation finishes. The implementation
uses **`js_code_before_wait`** for fill + click, then **`wait_for`** (default:
`js:() => !document.getElementById('FAOCaptcha_CaptchaImage')`) so HTML is scraped
after the captcha image is gone. Override with POST field **`wait_for`** (same
formats as crawl4ai `smart_wait`: `css:...`, `js:...`).

### `POST /crawl-captcha` body

| Field | Required | Description |
|-------|----------|-------------|
| `url` | yes | First page (captcha gate). |
| `follow_urls` | no | List of extra URLs in the **same** browser session after solve: absolute URL, relative path, or `?query=` on same path. |
| `wait_for` | no | Post-submit wait condition (default: captcha image absent). |
| `llm_extract` | no | If true, run `LLMExtractionStrategy` on each scraped page. |
| `llm_instruction` | if `llm_extract` | Extraction prompt. |
| `llm_schema` | no | JSON schema (object or string) for structured output. |
| `llm_provider` / `llm_temperature` | no | Same as other LLM routes; uses server `config`. |

**LLM credentials:** Follow the same rules as the rest of crawl4ai: set provider keys in
the **process environment** (e.g. Docker Compose `env_file` / `environment` as in
[`docker-compose.yml`](docker-compose.yml), or `export OPENAI_API_KEY=…` for local runs).
The library loads a root **`.env`** via crawl4ai’s own `load_dotenv()` where applicable;
`LLMConfig` can also resolve **`api_token="env:OPENAI_API_KEY"`** (see crawl4ai extraction /
`LLMConfig` docs). Optional **`llm.api_key`** in `deploy/docker/config.yml` overrides for
this server only.

### Response

- Top-level **`html`**, **`markdown`**, **`success`**, **`captcha_answer`**, **`error`**: primary page (index 0 in `pages`).
- **`pages`**: list of `{ url, html, markdown, success, error, extracted_content }` for primary + each `follow_urls` entry. `extracted_content` is parsed JSON when the LLM returns JSON.

### Session / cookie

Each request uses a **unique** `session_id` inside one `AsyncWebCrawler` context so
concurrent API calls do not share a Playwright page. The **`FAO-CAPTCHA`** cookie
applies to follow-up navigations **within that single request** when you pass
`follow_urls`.
