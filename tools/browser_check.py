import asyncio, json, sys, time
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(viewport={"width": 1000, "height": 1100})
        base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8912"
        await page.goto(base + "/", wait_until="networkidle")
        await page.wait_for_function("document.querySelectorAll('#model option').length > 0", timeout=15000)
        models = await page.eval_on_selector_all("#model option", "els => els.map(e => e.value)")
        print("base:", base)
        print("models:", models)
        await page.fill("#prompt", "a cyberpunk fox in the rain, cinematic")
        await page.select_option("#size", "1024x1536")
        await page.click("#go")
        await page.wait_for_function("(() => { const i = document.getElementById('img'); return i.style.display === 'block' && i.complete && i.naturalWidth > 0; })()", timeout=90000)
        src = await page.eval_on_selector("#img", "el => el.src")
        meta = await page.text_content("#meta")
        dims = await page.eval_on_selector("#img", "el => el.complete ? [el.naturalWidth, el.naturalHeight] : null")
        ok = bool(dims and dims[0] > 0)
        print("img src:", src)
        print("meta:", meta)
        print("image dims:", dims)
        await page.screenshot(path="docs/demo_page.png", full_page=True)
        await browser.close()

asyncio.run(main())
