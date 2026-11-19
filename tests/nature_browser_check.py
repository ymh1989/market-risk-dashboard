"""실제 영상 픽셀·재생·반복·전환·접근성과 모바일 화면을 브라우저로 확인합니다."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
from threading import Thread

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp/nature-qa"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def frame(page):
    image = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    w, h = image.size
    return image.crop((int(w * .1), int(h * .22), int(w * .9), int(h * .7)))


def verify_pixels(image):
    stats = ImageStat.Stat(image)
    assert max(stats.stddev) > 12, "풍경 픽셀의 변화가 없어 빈 화면일 수 있습니다."
    assert sum(stats.mean) / 3 > 20, "풍경 화면이 너무 어둡거나 비어 있습니다."
    return {"mean": [round(v, 1) for v in stats.mean], "variation": [round(v, 1) for v in stats.stddev]}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    result, errors, requests = {}, [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.getenv("NATURE_BROWSER"))
            context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            try:
                page.goto(base + "/nature-lab.html")
                page.wait_for_function("document.body.dataset.ready === 'true'")
                page.wait_for_timeout(1200)
                result["desktop"] = verify_pixels(frame(page))
                page.screenshot(path=str(OUTPUT / "desktop.png"))
                before = frame(page)
                page.wait_for_timeout(1200)
                movement = ImageStat.Stat(ImageChops.difference(before, frame(page))).mean
                assert max(movement) > .4, "영상이 실제로 움직이지 않습니다."
                assert not any("forest.mp4" in u or "alps.mp4" in u for u in requests)
                page.mouse.move(100, 320)
                page.wait_for_timeout(500)
                old = page.locator("#landscape").evaluate("e => getComputedStyle(e).transform")
                page.mouse.move(1300, 450)
                page.wait_for_timeout(800)
                assert old != page.locator("#landscape").evaluate("e => getComputedStyle(e).transform")
                page.get_by_role("button", name="일시정지", exact=True).click()
                times = page.locator("video").evaluate_all("vs => vs.map(v => v.currentTime)")
                page.wait_for_timeout(500)
                assert times == page.locator("video").evaluate_all("vs => vs.map(v => v.currentTime)")
                for name, label in [("forest", "숲이 깨어나는 시간"), ("alps", "알프스의 능선")]:
                    page.get_by_role("button", name="풍경 선택", exact=True).click()
                    page.get_by_role("button", name=label, exact=True).click()
                    page.wait_for_function("id => document.body.dataset.scene === id && document.body.dataset.loading === 'false'", arg=name)
                    result[name] = verify_pixels(frame(page))
                    page.screenshot(path=str(OUTPUT / f"{name}.png"))
                page.get_by_role("button", name="재생", exact=True).click()
                page.wait_for_timeout(400)
                page.locator("video").evaluate_all("vs => vs.filter(v => !v.paused).forEach(v => v.currentTime = v.duration - .8)")
                page.wait_for_function("document.body.dataset.transition === 'true'")
                page.wait_for_timeout(600)
                result["loopMidpoint"] = verify_pixels(frame(page))
                page.wait_for_function("document.body.dataset.transition === 'false'")
                assert page.locator("video").evaluate_all("vs => vs.filter(v => !v.paused).length") == 1
                page.get_by_role("combobox", name="재생 속도").select_option("0.5")
                assert page.locator("video").evaluate_all("vs => vs.every(v => v.playbackRate === .5)")
                page.mouse.click(700, 300)
                page.wait_for_timeout(6000)
                assert "idle" in page.locator("body").get_attribute("class")
                page.mouse.move(710, 310)
                assert "idle" not in (page.locator("body").get_attribute("class") or "")
                page.get_by_role("button", name="전체 화면", exact=True).click()
                page.wait_for_function("document.fullscreenElement !== null")
                page.get_by_role("button", name="전체 화면 종료", exact=True).click()
                page.wait_for_function("document.fullscreenElement === null")
                result["fullscreen"] = True
                # 숨김 이벤트의 중단·복귀 처리와 사용자의 일시정지 보존을 검증합니다.
                for paused in [False, True]:
                    if paused:
                        page.get_by_role("button", name="일시정지", exact=True).click()
                    page.evaluate("""() => {
                        Object.defineProperty(document, 'hidden', { configurable: true, value: true });
                        document.dispatchEvent(new Event('visibilitychange'));
                    }""")
                    assert page.locator("video").evaluate_all("vs => vs.every(v => v.paused)")
                    page.evaluate("""() => {
                        delete document.hidden;
                        document.dispatchEvent(new Event('visibilitychange'));
                    }""")
                    page.wait_for_timeout(250)
                    assert page.locator("video").evaluate_all("vs => vs.every(v => v.paused)") == paused
                page.get_by_role("button", name="재생", exact=True).click()
                result["visibilityHandler"] = True
                for width, height in [(390, 844), (320, 680), (844, 390), (2560, 1080)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.mouse.move(50, 80)
                    page.wait_for_timeout(300)
                    result[str(width)] = verify_pixels(frame(page))
                    page.screenshot(path=str(OUTPUT / f"viewport-{width}.png"))
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    for selector in (".dock", ".identity", ".clock", ".scene-heading"):
                        box = page.locator(selector).bounding_box()
                        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
                        assert box["y"] >= 0 and box["y"] + box["height"] <= height + 1
                    page.get_by_role("button", name="풍경 선택", exact=True).click()
                    page.screenshot(path=str(OUTPUT / f"picker-{width}.png"))
                    page.keyboard.press("Escape")
                    assert page.locator("#picker").is_hidden()
                for label in ["산과 물 사이", "숲이 깨어나는 시간", "알프스의 능선"]:
                    page.get_by_role("button", name="풍경 선택", exact=True).click()
                    page.get_by_role("button", name=label, exact=True).click()
                page.wait_for_function("document.body.dataset.scene === 'alps' && document.body.dataset.loading === 'false' && document.body.dataset.transition !== 'true'")
                assert page.locator("video").evaluate_all("vs => vs.filter(v => !v.paused).length") == 1
                result["rapidSelection"] = True
                page.emulate_media(reduced_motion="reduce")
                page.reload()
                page.wait_for_function("document.body.dataset.ready === 'true'")
                assert page.locator("video").evaluate_all("vs => vs.every(v => v.paused)")
                assert page.get_by_role("button", name="재생", exact=True).is_visible()
                result["reducedMotion"] = True
                # 영상 실패 시 포스터를 유지하고 재시도할 수 있어야 합니다.
                page.route("**/water.mp4", lambda route: route.abort())
                page.reload()
                page.get_by_role("button", name="다시 불러오기").wait_for()
                result["fallback"] = verify_pixels(frame(page))
                page.unroute("**/water.mp4")
                page.get_by_role("button", name="다시 불러오기").click()
                page.wait_for_function("document.body.dataset.ready === 'true'")
                result["retry"] = True
                assert all(url.startswith(base + "/") for url in requests)
                # HTML과 동봉 미디어만으로 네트워크 없이 열리는지 확인합니다.
                context.set_offline(True)
                page.goto((ROOT / "nature-lab.html").as_uri())
                page.wait_for_function("document.body.dataset.ready === 'true'")
                result["offline"] = verify_pixels(frame(page))
                context.set_offline(False)
                for filename in ["snow-lab.html", "chamber-lab.html"]:
                    page.goto(base + "/" + filename)
                    for width in [1440, 390, 320]:
                        page.set_viewport_size({"width": width, "height": 844})
                        link = page.get_by_role("link", name="창밖 열기", exact=True)
                        assert link.is_visible()
                        box = link.bounding_box()
                        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
                assert not errors, errors
                # 기존 시뮬레이터의 외부 요청 여부는 이 검사 대상에서 제외합니다.
                result["videoMotion"] = [round(v, 2) for v in movement]
                result["errors"] = errors
                result["lazyMedia"] = True
                (OUTPUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except Exception:
                page.screenshot(path=str(OUTPUT / "failure.png"))
                raise
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


if __name__ == "__main__":
    main()
