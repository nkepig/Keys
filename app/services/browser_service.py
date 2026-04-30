"""Browser 自动化公共方法服务。

封装 Chromium (DrissionPage) 的启动、关闭、Cloudflare Turnstile 绕过，
以及 ddddocr 验证码识别等通用浏览器操作。
"""

import os
import platform
import random
import shutil
import tempfile
import time
from typing import Optional

from DrissionPage import Chromium, ChromiumOptions
from loguru import logger


class AutoBrowseService:
    EXTENSION_MANIFEST = """{
    "manifest_version": 3,
    "name": "Turnstile Patcher",
    "version": "2.1",
    "content_scripts": [
        {
            "js": ["./script.js"],
            "matches": ["<all_urls>"],
            "run_at": "document_start",
            "all_frames": true,
            "world": "MAIN"
        }
    ]
}"""

    EXTENSION_SCRIPT = """function getRandomInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}
let screenX = getRandomInt(800, 1200);
let screenY = getRandomInt(400, 600);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: screenX });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: screenY });"""

    IFRAME_PATCH_SCRIPT = """
window.dtp = 1;
function getRandomInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}
let screenX = getRandomInt(800, 1200);
let screenY = getRandomInt(400, 600);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: screenX });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: screenY });"""

    def __init__(
        self,
        incognito: bool = False,
        headless: bool = False,
        enable_turnstile_bypass: bool = True,
        browser_path: str = None,
        local_port: Optional[int] = None,
    ) -> None:
        self.co = ChromiumOptions()
        if local_port is not None:
            self.co.set_local_port(local_port)
        else:
            # 未指定端口时由 DrissionPage 在区间内自动占用空闲端口，关闭后清理临时用户目录
            self.co.auto_port()
        if incognito:
            self.co.incognito()
        if headless:
            self.co.headless(True)
            self.co.set_argument("--headless=new")

        if browser_path:
            self.co.set_paths(browser_path=browser_path)

        if platform.system() == "Linux":
            try:
                self.co.set_argument("--no-sandbox")
                self.co.set_argument("--disable-dev-shm-usage")
                self.co.set_argument("--window-size=1920,1080")
            except Exception as e:
                logger.warning("添加 Linux 浏览器参数失败: {}", e)

        self.temp_extension_dir = None
        if enable_turnstile_bypass:
            extension_path = self._create_turnstile_extension()
            if extension_path and os.path.exists(extension_path):
                self.co.add_extension(extension_path)

        self.browser = Chromium(self.co)
        self.tab = self.browser.latest_tab
        self.ocr = None
        self._ocr_initialized = False

    def _create_turnstile_extension(self) -> Optional[str]:
        try:
            temp_dir = tempfile.mkdtemp(prefix="turnstile_patch_")
            self.temp_extension_dir = temp_dir
            manifest_path = os.path.join(temp_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                f.write(self.EXTENSION_MANIFEST)
            script_path = os.path.join(temp_dir, "script.js")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(self.EXTENSION_SCRIPT)
            return temp_dir
        except Exception as e:
            logger.error("创建 Turnstile 扩展失败: {}", e)
            return None

    def _cleanup_extension(self) -> None:
        if self.temp_extension_dir and os.path.exists(self.temp_extension_dir):
            try:
                shutil.rmtree(self.temp_extension_dir)
            except Exception:
                pass
            self.temp_extension_dir = None

    def _init_ocr_model(self) -> None:
        if self._ocr_initialized:
            return
        import ddddocr

        self.ocr = ddddocr.DdddOcr()
        self._ocr_initialized = True

    async def recognize_captcha(self, image_bytes: bytes) -> str:
        self._init_ocr_model()
        result = self.ocr.classification(image_bytes)
        return result.strip() if result else ""

    def detect_turnstile(self) -> bool:
        try:
            page_title = self.tab.title
            if "Just a moment" in page_title or "Verify you are human" in page_title:
                return True
            page_html = self.tab.html
            if "Cloudflare" in page_html and ("challenge" in page_html or "turnstile" in page_html):
                return True
            turnstile_response = self.tab.ele("@name=cf-turnstile-response", timeout=2)
            if turnstile_response:
                return True
            turnstile_iframe = self.tab.ele("tag:iframe[src*='challenges.cloudflare.com']", timeout=2)
            if turnstile_iframe:
                return True
            has_turnstile = self.tab.run_js(
                "try { return typeof turnstile !== 'undefined'; } catch(e) { return false; }"
            )
            if has_turnstile:
                return True
        except Exception:
            pass
        return False

    def bypass_turnstile(self, max_attempts: int = 20, wait_interval: float = 2.0) -> Optional[str]:
        logger.info("开始 Turnstile 绕过，最多尝试 {} 次...", max_attempts)
        time.sleep(3)
        for attempt in range(max_attempts):
            try:
                page_title = self.tab.title
                if "Just a moment" not in page_title and "Verify you are human" not in page_title:
                    page_html = self.tab.html
                    if len(page_html) > 5000 and "challenge" not in page_html.lower():
                        logger.info("第 {} 次尝试：页面已通过验证（标题判断）", attempt + 1)
                        return "verified"
                turnstile_response = self.tab.run_js(
                    "try { return turnstile.getResponse(); } catch(e) { return null; }"
                )
                if turnstile_response:
                    logger.info("第 {} 次尝试：获取到 Turnstile token", attempt + 1)
                    return turnstile_response
                challenge_solution = self.tab.ele("@name=cf-turnstile-response", timeout=2)
                if challenge_solution:
                    challenge_wrapper = challenge_solution.parent()
                    if challenge_wrapper:
                        try:
                            challenge_iframe = challenge_wrapper.shadow_root.ele("tag:iframe", timeout=2)
                            if challenge_iframe:
                                challenge_iframe.run_js(self.IFRAME_PATCH_SCRIPT)
                                time.sleep(0.5)
                                challenge_iframe_body = challenge_iframe.ele("tag:body")
                                if challenge_iframe_body and challenge_iframe_body.shadow_root:
                                    challenge_button = challenge_iframe_body.shadow_root.ele("tag:input", timeout=2)
                                    if challenge_button:
                                        challenge_button.click()
                                        logger.info("第 {} 次尝试：点击验证按钮", attempt + 1)
                                        time.sleep(2)
                        except Exception as e:
                            logger.debug("处理 iframe 失败: {}", e)
                if attempt > 0 and attempt % 5 == 0:
                    logger.info("第 {} 次尝试：刷新页面重试", attempt + 1)
                    self.tab.refresh()
                    time.sleep(3)
            except Exception as e:
                logger.debug("第 {} 次尝试异常: {}", attempt + 1, e)
            time.sleep(wait_interval)
        return None

    def close(self) -> None:
        if self.browser:
            self.browser.quit()
        self._cleanup_extension()
