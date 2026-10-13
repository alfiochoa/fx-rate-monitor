"""Real screenshots with headless Playwright against a running app: python scripts/shots.py http://localhost:8501"""
import sys

from playwright.sync_api import sync_playwright

url = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, scheme, vp in [("dashboard", "light", (1280, 2350)), ("dashboard-dark", "dark", (1280, 2350)), ("dashboard-mobile", "light", (390, 3300))]:
        pg = b.new_context(viewport={"width": vp[0], "height": vp[1]}, color_scheme=scheme).new_page()
        pg.goto(url)
        pg.wait_for_selector("text=What is my invoice worth", timeout=60000)
        pg.wait_for_timeout(3000)
        pg.screenshot(path=f"docs/{name}.png")
    b.close()
