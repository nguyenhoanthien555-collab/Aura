"""
ChatGPT Browser Bridge for Aura.

Launches a headless anti-detect AsyncCamoufox browser session using the user's decrypted
Opera GX cookies, maintaining a persistent, warm ChatGPT Web session.
Bypasses Cloudflare Turnstile, Sentinel PoW, and bot detection automatically.

Provides a fast HTTP API on 127.0.0.1:8765 for Aura's ChatGPTWebProvider.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import re
import sys
import time
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [Bridge] %(message)s")
logger = logging.getLogger("chatgpt_bridge")

COOKIES_PATH = r"d:\AURA\opera_chatgpt_cookies.json"
CHATGPT_URL = "https://chatgpt.com"
DEBUG_SCREENSHOT = r"d:\AURA\bridge_debug.png"

browser_lock = asyncio.Lock()
browser_instance = None
browser_context = None
browser_page = None


def clean_luna_response(raw: str) -> str:
    """Strips meta-commentary, leading reasoning markers, and quotes."""
    text = (raw or "").strip()
    if not text:
        return ""

    response_marker = re.search(
        r"(?:Here's my response|Here is my response|My response is)[:\s]*\n*[\"“]?([^\"”]+)[\"”]?",
        text,
        re.IGNORECASE,
    )
    if response_marker and response_marker.group(1).strip():
        return response_marker.group(1).strip()

    reconsider_idx = text.lower().find("actually, let me reconsider")
    if reconsider_idx > 0:
        text = text[:reconsider_idx].strip()

    text = re.sub(r"^(?:curiosity|thought|thinking)\.?\s*", "", text, flags=re.IGNORECASE)

    if (text.startswith('"') and text.endswith('"')) or (text.startswith("“") and text.endswith("”")):
        text = text[1:-1].strip()

    return text


def load_cookies():
    if not os.path.exists(COOKIES_PATH):
        logger.error("Cookies file not found at %s", COOKIES_PATH)
        return []
    with open(COOKIES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    cookies = []
    for k, v in data.items():
        cookies.append({
            "name": k,
            "value": v,
            "domain": ".chatgpt.com" if not k.startswith("__Host") else "chatgpt.com",
            "path": "/",
            "secure": True,
        })
    return cookies


async def ensure_browser():
    global browser_instance, browser_context, browser_page
    if browser_page is not None and not browser_page.is_closed():
        return browser_page

    logger.info("Initializing AsyncCamoufox anti-detect browser...")
    from camoufox.async_api import AsyncCamoufox

    cookies = load_cookies()
    browser_instance = await AsyncCamoufox(headless=True).__aenter__()
    browser_context = await browser_instance.new_context()
    if cookies:
        await browser_context.add_cookies(cookies)

    browser_page = await browser_context.new_page()
    logger.info("Navigating to %s ...", CHATGPT_URL)
    await browser_page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)

    try:
        await browser_page.wait_for_selector("#prompt-textarea, [contenteditable='true']", timeout=30000)
        logger.info("ChatGPT Web ready and authenticated.")
    except Exception as e:
        logger.warning("Prompt textarea not immediately found: %s", e)

    return browser_page


async def send_prompt_to_page(page, prompt: str):
    # Close any blocking dialogs/banners if present
    stay_logged_out = page.locator("button:has-text('Stay logged out'), [aria-label='Close']")
    if await stay_logged_out.count() > 0:
        try:
            await stay_logged_out.first.click(timeout=1000)
        except Exception:
            pass

    prompt_el = page.locator("#prompt-textarea, [contenteditable='true']").first
    await prompt_el.click()
    await prompt_el.fill(prompt)
    await prompt_el.dispatch_event("input")
    await asyncio.sleep(0.3)

    send_btn = page.locator("button[data-testid='send-button'], button[aria-label*='Send'], button[data-testid*='send']")
    sent = False
    if await send_btn.count() > 0:
        btn = send_btn.first
        for _ in range(25):
            if await btn.is_enabled():
                await btn.click()
                logger.info("Clicked send button.")
                sent = True
                break
            await asyncio.sleep(0.1)

    if not sent:
        logger.info("Send button not enabled/found, pressing Enter key.")
        await prompt_el.press("Enter")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up browser in background on startup
    asyncio.create_task(ensure_browser())
    yield
    # Cleanup on shutdown
    global browser_instance
    if browser_instance is not None:
        try:
            await browser_instance.close()
        except Exception:
            pass


app = FastAPI(title="ChatGPT Browser Bridge", version="1.0.0", lifespan=lifespan)


class ChatRequest(BaseModel):
    prompt: str
    stream: bool = False
    model: str = "auto"


@app.get("/health")
async def health():
    is_ready = browser_page is not None and not browser_page.is_closed()
    return {
        "status": "ok" if is_ready else "starting",
        "provider": "chatgpt_web",
        "model": "gpt-5.6-luna",
    }


@app.get("/screenshot")
async def screenshot():
    if browser_page is not None and not browser_page.is_closed():
        await browser_page.screenshot(path=DEBUG_SCREENSHOT)
        return FileResponse(DEBUG_SCREENSHOT, media_type="image/png")
    return {"status": "browser_not_ready"}


async def stream_generator(prompt: str) -> AsyncIterator[str]:
    async with browser_lock:
        page = await ensure_browser()
        initial_count = await page.locator("[data-message-author-role='assistant']").count()
        await send_prompt_to_page(page, prompt)

        target_index = initial_count
        start_wait = time.time()
        while await page.locator("[data-message-author-role='assistant']").count() <= target_index:
            if time.time() - start_wait > 35.0:
                await page.screenshot(path=DEBUG_SCREENSHOT)
                logger.error("Timeout waiting for assistant response in stream. Screenshot saved to %s", DEBUG_SCREENSHOT)
                yield f"data: {json.dumps({'error': 'Timeout waiting for ChatGPT response'})}\n\n"
                return
            await asyncio.sleep(0.1)

        assistant_el = page.locator("[data-message-author-role='assistant']").nth(target_index)

        full_text = ""
        # 1. Wait for assistant to begin outputting tokens
        start_gen = time.time()
        while len(full_text) == 0 and time.time() - start_gen < 25.0:
            try:
                full_text = (await assistant_el.text_content() or "").strip()
            except Exception:
                pass
            if len(full_text) > 0:
                break
            await asyncio.sleep(0.1)

        last_len = len(full_text)
        if last_len > 0:
            yield f"data: {json.dumps({'chunk': full_text})}\n\n"

        idle_ticks = 0
        while True:
            try:
                full_text = (await assistant_el.text_content() or "").strip()
            except Exception:
                break

            if len(full_text) > last_len:
                delta = full_text[last_len:]
                last_len = len(full_text)
                idle_ticks = 0
                yield f"data: {json.dumps({'chunk': delta})}\n\n"
            else:
                idle_ticks += 1

            stop_btn = page.locator("button[data-testid='stop-button']")
            is_generating = (await stop_btn.count() > 0) and (await stop_btn.first.is_visible())

            if (not is_generating and idle_ticks > 8) or idle_ticks > 25:
                final_text = (await assistant_el.text_content() or "").strip()
                if len(final_text) > last_len:
                    yield f"data: {json.dumps({'chunk': final_text[last_len:]})}\n\n"
                break

            await asyncio.sleep(0.1)

        cleaned = clean_luna_response(full_text)
        yield f"data: {json.dumps({'done': True, 'text': cleaned})}\n\n"
        yield "data: [DONE]\n\n"


@app.post("/chat")
async def chat(req: ChatRequest):
    if req.stream:
        return StreamingResponse(stream_generator(req.prompt), media_type="text/event-stream")

    async with browser_lock:
        page = await ensure_browser()
        initial_count = await page.locator("[data-message-author-role='assistant']").count()
        await send_prompt_to_page(page, req.prompt)

        target_index = initial_count
        start_wait = time.time()
        while await page.locator("[data-message-author-role='assistant']").count() <= target_index:
            if time.time() - start_wait > 35.0:
                await page.screenshot(path=DEBUG_SCREENSHOT)
                logger.error("Timeout waiting for assistant response in chat. Screenshot saved to %s", DEBUG_SCREENSHOT)
                raise HTTPException(status_code=504, detail="Timeout waiting for assistant response")
            await asyncio.sleep(0.1)

        assistant_el = page.locator("[data-message-author-role='assistant']").nth(target_index)

        full_text = ""
        start_gen = time.time()
        while len(full_text) == 0 and time.time() - start_gen < 25.0:
            try:
                full_text = (await assistant_el.text_content() or "").strip()
            except Exception:
                pass
            if len(full_text) > 0:
                break
            await asyncio.sleep(0.1)

        idle_ticks = 0
        last_len = len(full_text)
        while True:
            try:
                full_text = (await assistant_el.text_content() or "").strip()
            except Exception:
                break

            if len(full_text) > last_len:
                last_len = len(full_text)
                idle_ticks = 0
            else:
                idle_ticks += 1

            stop_btn = page.locator("button[data-testid='stop-button']")
            is_generating = (await stop_btn.count() > 0) and (await stop_btn.first.is_visible())

            if (not is_generating and idle_ticks > 8) or idle_ticks > 25:
                break
            await asyncio.sleep(0.1)

        cleaned = clean_luna_response(await assistant_el.text_content() or "")
        return {
            "text": cleaned,
            "provider": "chatgpt_web",
            "model": "gpt-5.6-luna",
        }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
