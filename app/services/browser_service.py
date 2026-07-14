"""Browser 自动化公共方法服务。

封装 Chromium (DrissionPage) 的启动、关闭、Cloudflare Turnstile 绕过，
以及 ddddocr 验证码识别等通用浏览器操作。
"""

import os
import platform
import re
import shutil
import tempfile
import time
from typing import Optional

from DrissionPage import Chromium, ChromiumOptions
from loguru import logger

_LINUX_CHROME_CANDIDATES = (
    "/opt/google/chrome/chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
)
_MAC_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


def resolve_browser_path(browser_path: str | None) -> str | None:
    if browser_path:
        path = browser_path.strip()
        if os.path.isfile(path):
            return path
        logger.warning("指定的浏览器路径不存在，改为自动探测: {}", path)
    candidates = (
        _MAC_CHROME_CANDIDATES if platform.system() == "Darwin" else _LINUX_CHROME_CANDIDATES
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
    ):
        found = shutil.which(name)
        if found:
            return found
    return None


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

    EXTENSION_SCRIPT = """(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  } catch (e) {}
  function getRandomInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
  }
  let screenX = getRandomInt(800, 1200);
  let screenY = getRandomInt(400, 600);
  Object.defineProperty(MouseEvent.prototype, 'screenX', { get() { return screenX; } });
  Object.defineProperty(MouseEvent.prototype, 'screenY', { get() { return screenY; } });
})();"""

    IFRAME_PATCH_SCRIPT = """
window.dtp = 1;
function getRandomInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}
let screenX = getRandomInt(800, 1200);
let screenY = getRandomInt(400, 600);
Object.defineProperty(MouseEvent.prototype, 'screenX', { get() { return screenX; } });
Object.defineProperty(MouseEvent.prototype, 'screenY', { get() { return screenY; } });
"""

    def __init__(
        self,
        incognito: bool = False,
        headless: bool = False,
        enable_turnstile_bypass: bool = True,
        browser_path: str = None,
        local_port: Optional[int] = None,
    ) -> None:
        self.headless = bool(headless)
        self.incognito = bool(incognito)
        resolved_path = resolve_browser_path(browser_path)
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

        if resolved_path:
            self.co.set_browser_path(path=resolved_path)

        # CF 会检测 automation 标志与异常 UA；尽量贴近真实浏览器
        try:
            self.co.set_argument("--disable-blink-features=AutomationControlled")
            self.co.set_argument("--disable-infobars")
            self.co.set_pref("credentials_enable_service", False)
            self.co.set_pref("profile.password_manager_enabled", False)
        except Exception as e:
            logger.warning("添加反检测参数失败: {}", e)

        if platform.system() == "Linux":
            try:
                self.co.set_argument("--no-sandbox")
                self.co.set_argument("--disable-dev-shm-usage")
                self.co.set_argument("--window-size=1920,1080")
            except Exception as e:
                logger.warning("添加 Linux 浏览器参数失败: {}", e)

        self.temp_extension_dir = None
        # 无痕模式默认不加载扩展，扩展对 Turnstile 帮助有限且易冲突
        if enable_turnstile_bypass and not incognito:
            extension_path = self._create_turnstile_extension()
            if extension_path and os.path.exists(extension_path):
                self.co.add_extension(extension_path)

        self.browser = Chromium(self.co)
        self.tab = self.browser.latest_tab
        self._align_user_agent()
        self.ocr = None
        self._ocr_initialized = False
        logger.info(
            "浏览器已启动: headless={} incognito={} path={}",
            self.headless,
            self.incognito,
            resolved_path or "(default)",
        )

    def _align_user_agent(self) -> None:
        """让 UA 中的 Chrome 主版本与真实浏览器一致，避免 fingerprint 直接拦 CF。"""
        try:
            ver = self.tab.run_js(
                "return navigator.userAgentData ? navigator.userAgentData.brands : null"
            )
            major = None
            if isinstance(ver, list):
                for brand in ver:
                    if isinstance(brand, dict) and "Chromium" in str(brand.get("brand", "")):
                        major = str(brand.get("version", "")).split(".")[0]
                        break
            if not major:
                ua = self.tab.run_js("return navigator.userAgent") or ""
                m = re.search(r"Chrome/(\d+)", ua)
                major = m.group(1) if m else "120"
            if platform.system() == "Darwin":
                ua = (
                    f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
                )
            elif platform.system() == "Windows":
                ua = (
                    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
                )
            else:
                ua = (
                    f"Mozilla/5.0 (X11; Linux x86_64) "
                    f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
                )
            self.tab.set.user_agent(ua)
            logger.debug("已对齐 User-Agent: {}", ua)
        except Exception as e:
            logger.debug("对齐 User-Agent 失败: {}", e)

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

    def _challenge_active(self) -> bool:
        """是否仍处于 Cloudflare 全页拦截（Just a moment / 请稍候），不是登录表单里的嵌入式 Turnstile。"""
        try:
            title = (self.tab.title or "").strip().lower()
            # 中英文全页盾标题
            title_markers = (
                "just a moment",
                "verify you are human",
                "attention required",
                "please wait",
                "请稍候",
                "稍候",
                "正在验证",
                "安全检查",
            )
            if any(m in title for m in title_markers):
                return True
            html = (self.tab.html or "").lower()
            # 全页挑战特征；不要仅凭 challenges.cloudflare.com iframe 判断——
            # Pastebin 登录页本身就长期挂着嵌入式 Turnstile 组件。
            interstitial = (
                "cf-browser-verification",
                "cf-challenge-running",
                "cdn-cgi/challenge-platform/h/",
                "cdn-cgi/challenge-platform/scripts/jsd",
            )
            if any(m in html for m in interstitial):
                # 有主内容表单时，更可能是嵌入式组件而非全页盾
                if self.tab.ele("#loginform-username", timeout=0.3) or self.tab.ele(
                    "#loginform-password", timeout=0.3
                ):
                    return False
                return True
        except Exception:
            pass
        return False

    def detect_turnstile(self) -> bool:
        return self._challenge_active() or self._embedded_turnstile_pending()

    def _get_turnstile_token(self) -> Optional[str]:
        try:
            token = self.tab.run_js(
                """
                try {
                  const byApi = (window.turnstile && turnstile.getResponse)
                    ? turnstile.getResponse() : '';
                  if (byApi) return byApi;
                  const inputs = document.querySelectorAll(
                    'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]'
                  );
                  for (const el of inputs) {
                    if (el && el.value) return el.value;
                  }
                  return null;
                } catch (e) { return null; }
                """
            )
            if token and str(token).strip():
                return str(token).strip()
        except Exception:
            pass
        return None

    def _embedded_turnstile_pending(self) -> bool:
        """登录表单等页面上的嵌入式 Turnstile：有组件但还没有 token。"""
        try:
            if self._challenge_active():
                return False
            has_widget = bool(
                self.tab.ele("@name=cf-turnstile-response", timeout=0.5)
                or self.tab.ele("tag:iframe[src*='challenges.cloudflare.com']", timeout=0.5)
            )
            if not has_widget:
                return False
            return self._get_turnstile_token() is None
        except Exception:
            return False

    def _try_click_turnstile(self) -> bool:
        """尝试点击 Turnstile checkbox（结构经常变，多路径尝试）。"""
        try:
            # 已有 token 就不要再点，重复点击会重置组件
            if self._get_turnstile_token():
                return False

            challenge_solution = self.tab.ele("@name=cf-turnstile-response", timeout=1)
            challenge_iframe = None
            if challenge_solution:
                challenge_wrapper = challenge_solution.parent()
                if challenge_wrapper:
                    try:
                        if challenge_wrapper.shadow_root:
                            challenge_iframe = challenge_wrapper.shadow_root.ele(
                                "tag:iframe", timeout=1
                            )
                    except Exception:
                        pass
            if not challenge_iframe:
                challenge_iframe = self.tab.ele(
                    "tag:iframe[src*='challenges.cloudflare.com']", timeout=1
                )
            if not challenge_iframe:
                return False
            try:
                challenge_iframe.run_js(self.IFRAME_PATCH_SCRIPT)
            except Exception:
                pass
            time.sleep(0.3)

            # 路径1: shadow body > input/checkbox
            try:
                body = challenge_iframe.ele("tag:body", timeout=1)
                if body and body.shadow_root:
                    for selector in ("tag:input", ".cb-lb", "#challenge-stage", "tag:label"):
                        btn = body.shadow_root.ele(selector, timeout=0.5)
                        if btn:
                            btn.click(by_js=False)
                            return True
            except Exception:
                pass

            # 路径2: Actions 坐标点击 iframe 中心偏左（checkbox 常见位置）
            try:
                self.tab.actions.move_to(challenge_iframe, offset_x=28, offset_y=28).click()
                return True
            except Exception:
                pass

            # 路径3: 直接点 iframe
            try:
                challenge_iframe.click(by_js=False)
                return True
            except Exception:
                pass
        except Exception as e:
            logger.debug("点击 Turnstile 失败: {}", e)
        return False

    def _wait_until_clear(self, seconds: float) -> Optional[str]:
        """静等全页盾消失或拿到 token（请稍候类挑战常需干等，不能狂点）。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            token = self._get_turnstile_token()
            if token:
                return token
            if not self._challenge_active():
                html_len = len(self.tab.html or "")
                if html_len > 800:
                    return "verified"
                # 可能短暂空白，再等一会儿
            time.sleep(0.8)
        return self._get_turnstile_token()

    def _wait_token_after_click(self, seconds: float = 8.0) -> Optional[str]:
        """点击后等待 token，避免误判为失败后再次点击导致组件重置。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if not self._challenge_active():
                token = self._get_turnstile_token()
                if token:
                    return token
                if len(self.tab.html or "") > 800 and not self._embedded_turnstile_pending():
                    return "verified"
            else:
                token = self._get_turnstile_token()
                if token:
                    return token
            time.sleep(0.5)
        return self._get_turnstile_token()

    def bypass_turnstile(self, max_attempts: int = 20, wait_interval: float = 2.0) -> Optional[str]:
        if self.headless:
            logger.warning(
                "当前为 headless 模式，Cloudflare Turnstile 通过率很低；"
                "建议有界面环境用 headless=False，或使用 xvfb-run"
            )
        if self.incognito:
            logger.warning("当前为无痕模式，Cloudflare 通过率通常更低，建议登录阶段关闭无痕")

        interstitial = self._challenge_active()
        embedded = self._embedded_turnstile_pending()
        if not interstitial and not embedded:
            logger.info("未检测到需要处理的 Turnstile")
            return "verified"

        mode = "全页挑战" if interstitial else "嵌入式组件"
        logger.info("开始 Turnstile 绕过（{}），最多尝试 {} 次...", mode, max_attempts)

        # 「请稍候…」多数是托管 JS 挑战：先静等，狂点/刷新反而会重置
        if interstitial:
            logger.info("全页挑战先静等最多 25s（勿手动刷新浏览器）…")
            token = self._wait_until_clear(25.0)
            if token:
                logger.info("静等通过：{}", "token" if token != "verified" else "页面已放行")
                return token
            logger.info("静等未过，再尝试有限次数点击验证框…")

        clicked_once = False
        for attempt in range(max_attempts):
            try:
                if interstitial and not self._challenge_active():
                    html_len = len(self.tab.html or "")
                    if html_len > 800:
                        logger.info(
                            "第 {} 次尝试：全页挑战已消失（页面长度 {}）",
                            attempt + 1,
                            html_len,
                        )
                        return "verified"

                token = self._get_turnstile_token()
                if token:
                    logger.info("第 {} 次尝试：已拿到 Turnstile token", attempt + 1)
                    return token

                # 嵌入式或静等失败后的兜底：最多点 2 次，间隔拉长
                if (not clicked_once) or (clicked_once and attempt == 8):
                    clicked = self._try_click_turnstile()
                    if clicked:
                        clicked_once = True
                        logger.info("第 {} 次尝试：已点击验证框，等待结果…", attempt + 1)
                        token = self._wait_token_after_click(12.0)
                        if token:
                            logger.info("点击后已获得通过状态")
                            return token
                        logger.warning(
                            "点击后仍无 token（指纹/环境仍被拒）。继续静等，不再频繁点击。"
                        )
                        if interstitial:
                            token = self._wait_until_clear(15.0)
                            if token:
                                return token
                        continue

                # 全页盾尽量不刷新；刷新会丢掉挑战进度
            except Exception as e:
                logger.debug("第 {} 次尝试异常: {}", attempt + 1, e)
            time.sleep(wait_interval)

        title = ""
        try:
            title = self.tab.title or ""
        except Exception:
            pass
        logger.error(
            "Turnstile 绕过失败：title={!r} headless={} incognito={} mode={}。"
            "日志若长期停在「请稍候…」且点了无 token，说明自动化环境已被 CF 拒绝。"
            "请确认：1) 使用本机 Google Chrome（不要 /opt/google/...）2) 关闭无痕 "
            "3) 换网络/住宅 IP；仍不行需打码平台。",
            title,
            self.headless,
            self.incognito,
            mode,
        )
        return None

    def close(self) -> None:
        if self.browser:
            self.browser.quit()
        self._cleanup_extension()
