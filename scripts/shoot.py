from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8137/"
errs = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 860})
    pg.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_timeout(3500)
    pg.screenshot(path="scripts/shot_iso.png")
    print("ISO legend:", repr(pg.inner_text("#legend"))[:160])
    print("layers:", pg.evaluate("Object.keys(map.style._layers||{}).filter(l=>/iso|sgg|hosp/.test(l))"))
    # 고령화 뷰
    pg.click("button[data-view='aging']")
    pg.wait_for_timeout(2500)
    pg.screenshot(path="scripts/shot_aging.png")
    print("AGING legend:", repr(pg.inner_text("#legend"))[:160])
    # 바이베리엇 뷰
    pg.click("button[data-view='bivar']")
    pg.wait_for_timeout(1800)
    pg.screenshot(path="scripts/shot_bivar.png")
    print("BIVAR legend:", repr(pg.inner_text("#legend"))[:160])
    b.close()

print("=== console errors/warnings ===")
for e in errs[:40]:
    print(e)
print(f"(total {len(errs)})")
