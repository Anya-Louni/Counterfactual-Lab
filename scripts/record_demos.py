"""Record the README demo GIFs from the running app.

Needs the API on :8000 and the frontend on :3000. Each demo asks a question, approves the
plan, then shows the reach animation, a swipe between the real city and the scenario, and
the difference map.

uv run python scripts/record_demos.py
"""

import io
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
URL = "http://localhost:3000"
W, H = 1440, 900
GIF_W = 960

DEMOS = [
    ("demo_clinic", "What if a new clinic opened near Baraki?", "Walking", "Clinic"),
    ("demo_hospital", "What if the main hospital in Bab El Oued closed?", "Walking", "Hospital"),
    ("demo_road", "What if Rue Hassiba Ben Bouali were closed to traffic?", "Car", "Hospital"),
]


class Reel:
    def __init__(self, page: Page):
        self.page, self.frames = page, []

    def shot(self, ms: int = 120):
        try:
            raw = self.page.screenshot(timeout=90_000)
        except PlaywrightTimeout:  # software WebGL can stall a frame; skip it
            return
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        self.frames.append((img.resize((GIF_W, round(GIF_W * img.height / img.width)), Image.LANCZOS), ms))

    def film(self, seconds: float, fps: float = 7):
        end = time.time() + seconds
        while time.time() < end:
            t = time.time()
            self.shot(round(1000 / fps))
            time.sleep(max(0, 1 / fps - (time.time() - t)))

    def save(self, name: str):
        # One shared palette keeps colours stable from frame to frame.
        pal = self.frames[len(self.frames) // 2][0].quantize(colors=200, method=Image.Quantize.MEDIANCUT)
        imgs = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f, _ in self.frames]
        path = OUT / f"{name}.gif"
        imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=[d for _, d in self.frames],
                     loop=0, optimize=True)
        print(name, len(imgs), "frames", round(path.stat().st_size / 2**20, 1), "MB")


def click_text(page: Page, text: str):
    page.get_by_role("radio", name=text, exact=True).click()


def demo(page: Page, name: str, question: str, mode: str, category: str):
    page.goto(URL)
    page.wait_for_selector("canvas.maplibregl-canvas", timeout=120_000)
    page.get_by_text("Within 15 min").wait_for(timeout=60_000)
    click_text(page, mode)
    click_text(page, category)
    time.sleep(4)  # basemap tiles
    reel = Reel(page)
    reel.shot(1800)

    box = page.get_by_label("Your what-if question")
    box.click()
    for ch in question:  # visible typing
        box.type(ch)
        if ch == " ":
            reel.shot(60)
    reel.shot(400)
    box.press("Enter")
    reel.film(1.2, fps=3)
    page.get_by_text("Plan, waiting for you").wait_for(timeout=120_000)
    reel.shot(2600)

    page.get_by_role("button", name="Run scenario").click()
    reel.film(1.5, fps=3)
    page.get_by_text("What it means").wait_for(timeout=300_000)
    reel.film(5.5, fps=8)  # fly to the change and the growing reach
    reel.shot(1500)

    handle = page.get_by_label("Drag to compare the real city and the scenario")
    hb = handle.bounding_box()
    mx = page.locator("section.relative").first.bounding_box()
    y = hb["y"] + hb["height"] / 2
    page.mouse.move(hb["x"] + hb["width"] / 2, y)
    page.mouse.down()
    for frac in [i / 14 for i in range(7, 1, -1)] + [i / 14 for i in range(2, 13)] + [i / 14 for i in range(12, 6, -1)]:
        page.mouse.move(mx["x"] + mx["width"] * frac, y)
        time.sleep(0.05)
        reel.shot(110)
    page.mouse.up()
    reel.shot(800)

    click_text(page, "Difference")
    time.sleep(1)
    reel.shot(3500)
    reel.save(name)


def main():
    only = set(sys.argv[1:])
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for name, q, mode, cat in DEMOS:
            if not only or name in only:
                demo(page, name, q, mode, cat)
        browser.close()


if __name__ == "__main__":
    main()
