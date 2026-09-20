"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")

LinuxDo 每日签到 + LDC 积分站余额查询
- 论坛侧：登录 + 浏览主题帖（产生当日有效活跃，对应 LDC 每日登录奖励）
- 积分站：credit.linux.do 拉取 LDC 余额 / 信任等级（接口经源码核实：
  GET /api/v1/oauth/user-info，GET 请求无需 CSRF 头）
"""

import os
import json
import random
import time
import functools
from urllib.parse import urlparse
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate
from curl_cffi import requests
from bs4 import BeautifulSoup
from notify import NotificationManager


def retry_decorator(retries=3, min_delay=5, max_delay=10):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:  # 最后一次尝试
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}"
                    )
                    if attempt < retries - 1:
                        sleep_s = random.uniform(min_delay, max_delay)
                        logger.info(
                            f"将在 {sleep_s:.2f}s 后重试 ({min_delay}-{max_delay}s 随机延迟)"
                        )
                        time.sleep(sleep_s)
            return None

        return wrapper

    return decorator


os.environ.pop("DISPLAY", None)
os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = os.environ.get("LINUXDO_USERNAME")
PASSWORD = os.environ.get("LINUXDO_PASSWORD")
COOKIES = os.environ.get("LINUXDO_COOKIES", "").strip()  # 手动设置的 Cookie 字符串，优先使用
CREDIT_COOKIES = os.environ.get("LINUXDO_CREDIT_COOKIES", "").strip()  # credit.linux.do Cookie，可选
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]
# 点赞默认关闭：社区已取消点赞积分奖励，且脚本点赞有被判定为异常行为的风险
LIKE_ENABLED = os.environ.get("LIKE_ENABLED", "false").strip().lower() in [
    "true",
    "1",
    "on",
]
# LDC 积分站查询默认开启，失败不影响论坛签到结论
LDC_ENABLED = os.environ.get("LDC_ENABLED", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]
BROWSE_TOPIC_COUNT = max(1, min(20, int(os.environ.get("BROWSE_TOPIC_COUNT", "10") or "10")))

if not USERNAME:
    USERNAME = os.environ.get("USERNAME")
if not PASSWORD:
    PASSWORD = os.environ.get("PASSWORD")

HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
SESSION_URL = "https://linux.do/session"
CSRF_URL = "https://linux.do/session/csrf"
CREDIT_HOME_URL = "https://credit.linux.do/home"
CREDIT_USER_INFO_URL = "https://credit.linux.do/api/v1/oauth/user-info"
CONNECT_URL = "https://connect.linux.do/"

# Cloudflare 挑战页特征标题
CF_TITLE_KEYWORDS = ("请稍候", "just a moment", "attention required", "checking your browser",
                     "verify you are human", "请完成验证")
# Cloudflare 挑战页 HTML 特征（在完整 HTML 里找，不限头部）
CF_HTML_KEYWORDS = ("challenge-platform", "challenges.cloudflare.com", "cf-challenge",
                    "cf-turnstile", "cdn-cgi/challenge", "cf-error-details")

TRUST_LEVEL_NAMES = {
    0: "新用户",
    1: "基础用户",
    2: "成员",
    3: "活跃用户",
    4: "领导者",
}


class LinuxDoBrowser:
    def __init__(self) -> None:
        from sys import platform

        if platform == "linux" or platform == "linux2":
            platformIdentifier = "X11; Linux x86_64"
        elif platform == "darwin":
            platformIdentifier = "Macintosh; Intel Mac OS X 10_15_7"
        elif platform == "win32":
            platformIdentifier = "Windows NT 10.0; Win64; x64"
        else:
            platformIdentifier = "X11; Linux x86_64"

        co = (
            ChromiumOptions()
            .headless(True)
            .incognito(True)
            .set_argument("--no-sandbox")
            .set_argument("--disable-dev-shm-usage")
        )
        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        # 运行结果汇总
        self.summary = {
            "username": USERNAME or "-",
            "login": False,
            "login_method": "-",
            "topics_visited": 0,
            "likes": 0,
            "ldc": {},
            "connect_rows": [],
        }
        # 初始化通知管理器
        self.notifier = NotificationManager()

    # ---------------- 基础工具 ----------------

    @staticmethod
    def parse_cookie_string(cookie_str: str, domain: str = ".linux.do") -> list[dict]:
        """
        解析浏览器复制的 Cookie 字符串格式: "name1=value1; name2=value2"
        返回 DrissionPage 所需的 cookie 列表格式。
        """
        cookies = []
        for part in cookie_str.strip().split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                name = name.strip()
                value = value.strip()
                if not name:
                    continue
                cookies.append(
                    {
                        "name": name,
                        "value": value,
                        "domain": domain,
                        "path": "/",
                    }
                )
        return cookies

    @staticmethod
    def _is_cf_challenge(page) -> bool:
        """
        判断当前页面是否处于 Cloudflare 挑战页。
        注意：linux.do 正常页面（约 800KB HTML）里也内嵌了 challenge-platform
        等脚本引用，因此 HTML 关键字只对"小页面"生效（挑战页只有几 KB），
        否则正常论坛页会被永久误判为挑战页。
        """
        try:
            title = (page.title or "").strip().lower()
        except Exception:
            title = ""
        if any(k in title for k in CF_TITLE_KEYWORDS):
            return True
        try:
            html = page.html or ""
        except Exception:
            return False
        if len(html) > 50_000:
            # 论坛正常页远大于此，不可能是挑战页
            return False
        low = html.lower()
        return any(k in low for k in CF_HTML_KEYWORDS)

    @classmethod
    def _page_diag(cls, page, tag: str) -> None:
        """输出页面诊断信息（只读，任何异常仅降级为跳过）"""
        try:
            title = page.title or ""
        except Exception:
            title = "<err>"
        try:
            url = page.url
        except Exception:
            url = "<err>"
        try:
            html_len = len(page.html or "")
        except Exception:
            html_len = -1
        cf = "yes" if cls._is_cf_challenge(page) else "no"
        logger.info(f"[诊断:{tag}] title={title!r} url={url} html_len={html_len} cf_challenge={cf}")

    def _dump_debug(self, page, tag: str) -> None:
        """失败现场留证：截图 + HTML，供 GitHub Actions artifact 下载排查"""
        try:
            import os

            os.makedirs("debug", exist_ok=True)
            ts = time.strftime("%H%M%S")
            shot = f"debug/{tag}_{ts}.png"
            htmlf = f"debug/{tag}_{ts}.html"
            try:
                page.get_screenshot(shot)
            except Exception as e:
                logger.warning(f"截图失败: {e}")
                shot = ""
            try:
                with open(htmlf, "w", encoding="utf-8") as f:
                    f.write(page.html or "")
            except Exception as e:
                logger.warning(f"HTML 落盘失败: {e}")
                htmlf = ""
            saved = [p for p in (shot, htmlf) if p]
            logger.info(f"已保存失败现场: {saved if saved else '(无)'}")
        except Exception as e:
            logger.warning(f"保存调试信息异常: {e}")

    def _try_solve_turnstile(self, page) -> bool:
        """
        尝试点击 Cloudflare Turnstile 人机验证复选框。
        Turnstile 位于跨域 iframe 内无法直接操作 DOM，但可以先定位 iframe
        在页面中的坐标，再用鼠标事件点击复选框区域（约左侧 30px 处）。
        属于尽力而为：点击失败不影响原有等待逻辑。
        """
        try:
            rect_js = """
var f = document.querySelector('iframe[src*="challenges.cloudflare.com"]')
      || document.querySelector('iframe[title*=" Widget"]')
      || document.querySelector('.cf-turnstile iframe');
if (!f) return null;
var r = f.getBoundingClientRect();
return [r.x, r.y, r.width, r.height];
"""
            rect = page.run_js(rect_js)
            if not rect or len(rect) != 4 or rect[2] <= 0:
                return False
            x, y, w, h = rect
            # 复选框在 iframe 左侧偏中位置，加一点随机量模拟真人
            cx = x + min(30, w / 3) + random.uniform(-3, 3)
            cy = y + h / 2 + random.uniform(-3, 3)
            page.actions.move_to((cx, cy)).click()
            logger.info(f"已尝试点击 Turnstile 复选框 ({cx:.0f},{cy:.0f})")
            return True
        except Exception:
            return False

    def _wait_cf_clear(self, page, timeout: int = 45) -> bool:
        """等待 Cloudflare 挑战结束；期间尝试自动点击人机验证复选框"""
        deadline = time.time() + timeout
        clicked = False
        while time.time() < deadline:
            if not self._is_cf_challenge(page):
                return True
            if not clicked:
                clicked = self._try_solve_turnstile(page)
            time.sleep(3)
        return not self._is_cf_challenge(page)

    def _goto_with_cf_retry(self, page, url: str, attempts: int = 3, wait_cf: int = 45) -> bool:
        """打开页面并等待 Cloudflare 放行；挑战是间歇性的，失败时重试可显著提高成功率"""
        for i in range(attempts):
            try:
                page.get(url)
            except Exception as e:
                logger.warning(f"打开 {url} 异常: {e}")
            if self._wait_cf_clear(page, wait_cf):
                if i > 0:
                    logger.info(f"第 {i + 1} 次尝试通过 Cloudflare")
                return True
            logger.warning(f"Cloudflare 挑战未通过 ({i + 1}/{attempts})，重试...")
            time.sleep(random.uniform(3, 6))
        return False

    def _wait_login(self, timeout: int = 30) -> bool:
        """
        等待论坛登录态出现（严格判定，多信号融合）。
        注意：不能用 "avatar" in html 这类宽松判断 —— Discourse 未登录页面的
        HTML/JS 里也大量含 avatar 字符串，会误报登录成功。
        判定优先级:
        1. 同步 XHR 请求 /session/current.json 返回 current_user（最权威）
        2. 头部用户元素出现（多套选择器兜底，Discourse 版本间 id 可能变化）
        """
        selectors = ("@id=current-user", ".header-dropdown-toggle.current-user",
                     "#toggle-current-user", "li.current-user button")
        deadline = time.time() + timeout
        while time.time() < deadline:
            # 信号 1: 同步 XHR（页面同源，能直接读到登录态）
            try:
                res = self.page.run_js(
                    "var x=new XMLHttpRequest();"
                    "x.open('GET','/session/current.json',false);"
                    "try{x.send()}catch(e){return 'XHR_ERR'}"
                    "return (x.status===200 && x.responseText.indexOf('current_user')>-1) ? 'LOGGED_IN' : ('HTTP'+x.status);"
                )
            except Exception:
                res = None
            if res == "LOGGED_IN":
                return True
            # 信号 2: DOM 元素（XHR 被拦截或页面结构变化时兜底）
            for sel in selectors:
                try:
                    if self.page.ele(sel, timeout=1):
                        return True
                except Exception:
                    continue
            time.sleep(2)
        return False

    def _sync_session_cookies(self, domain_filter: str = "linux.do") -> int:
        """把浏览器当前页可见 Cookie 同步到 curl_cffi session（用于后续 API 请求）"""
        count = 0
        try:
            for ck in self.page.cookies():
                try:
                    item = ck.as_dict() if hasattr(ck, "as_dict") else dict(ck)
                except Exception:
                    continue
                dom = str(item.get("domain", ""))
                name = item.get("name")
                value = item.get("value")
                if not name or value is None:
                    continue
                if domain_filter not in dom:
                    continue
                self.session.cookies.set(name, value, domain=dom.lstrip(".") or "linux.do")
                count += 1
        except Exception as e:
            logger.warning(f"同步 Cookie 到 session 失败: {e}")
        logger.info(f"已同步 {count} 个 Cookie 到 requests session")
        return count

    def _get_domain_cookies(self, domain_substr: str) -> dict:
        """从浏览器读取指定域的 Cookie（需当前页在该域下）"""
        result = {}
        try:
            for ck in self.page.cookies():
                try:
                    item = ck.as_dict() if hasattr(ck, "as_dict") else dict(ck)
                except Exception:
                    continue
                dom = str(item.get("domain", ""))
                name = item.get("name")
                value = item.get("value")
                if not name or value is None:
                    continue
                if domain_substr in dom:
                    result[name] = value
        except Exception as e:
            logger.warning(f"读取 {domain_substr} Cookie 失败: {e}")
        return result

    # ---------------- 登录 ----------------

    def login_with_cookies(self, cookie_str: str) -> bool:
        """使用手动设置的 Cookie 直接登录，跳过账号密码流程"""
        logger.info("检测到手动 Cookie，尝试 Cookie 登录...")
        dp_cookies = self.parse_cookie_string(cookie_str)
        if not dp_cookies:
            logger.error("Cookie 解析失败或为空，无法使用 Cookie 登录")
            return False

        logger.info(f"成功解析 {len(dp_cookies)} 个 Cookie 条目")

        # 同步到 requests.Session，以便后续 API 请求（如 print_connect_info）使用
        for ck in dp_cookies:
            self.session.cookies.set(ck["name"], ck["value"], domain="linux.do")

        # 同步到 DrissionPage
        self.page.set.cookies(dp_cookies)
        # 提示：Cookie 登录必须包含 _t（长期记忆 Cookie）。只复制 _forum_session
        # 等临时 Cookie 是登不上的。names 不打印 value，只打印键名辅助排查。
        logger.info(f"Cookie 键名: {[c['name'] for c in dp_cookies]}")
        logger.info("Cookie 设置完成，导航至 linux.do...")
        if not self._goto_with_cf_retry(self.page, HOME_URL):
            logger.error("Cloudflare 挑战持续未通过，Cookie 登录终止")
            self._page_diag(self.page, "cookie_cf_fail")
            self._dump_debug(self.page, "cookie_cf_fail")
            return False
        time.sleep(5)

        ok = self._wait_login(30)
        if ok:
            logger.info("Cookie 登录验证成功")
        else:
            logger.error("Cookie 登录验证失败（页面已加载但未出现登录态），请检查 Cookie 是否包含 _t 且未过期")
            self._page_diag(self.page, "cookie_login_fail")
            self._dump_debug(self.page, "cookie_login_fail")
        return ok

    def login_by_password(self) -> bool:
        """在浏览器内完成账号密码登录（浏览器可承载 Cloudflare 挑战，比纯 HTTP 更稳）"""
        if not USERNAME or not PASSWORD:
            logger.warning("未提供账号密码，跳过浏览器登录")
            return False

        logger.info("开始账号密码登录（浏览器内）")
        try:
            if not self._goto_with_cf_retry(self.page, LOGIN_URL, wait_cf=60):
                logger.error("Cloudflare 挑战持续未通过，账号密码登录终止")
                self._page_diag(self.page, "pwd_cf_fail")
                self._dump_debug(self.page, "pwd_cf_fail")
                return False
            self._page_diag(self.page, "login_page_loaded")

            name_ele = pwd_ele = None
            # /login 直接打开时 Discourse 会弹出登录 modal；若未弹出，尝试点头部登录按钮唤起
            for attempt in range(2):
                name_ele = self.page.ele("#login-account-name", timeout=15)
                pwd_ele = self.page.ele("#login-account-password", timeout=5)
                if name_ele and pwd_ele:
                    break
                logger.info("登录表单未出现，尝试点击头部登录按钮唤起...")
                try:
                    header_btn = self.page.ele(".login-button", timeout=5) or self.page.ele(
                        "button.login-button", timeout=3
                    )
                    if header_btn:
                        header_btn.click()
                        time.sleep(3)
                except Exception:
                    pass

            if not (name_ele and pwd_ele):
                logger.error("未找到登录表单（页面非预期状态），已保存失败现场")
                self._page_diag(self.page, "pwd_no_form")
                self._dump_debug(self.page, "pwd_no_form")
                return False

            name_ele.input(USERNAME)
            time.sleep(random.uniform(0.5, 1.2))
            pwd_ele.input(PASSWORD)
            time.sleep(random.uniform(0.5, 1.2))

            login_btn = self.page.ele("#login-button", timeout=10)
            if not login_btn:
                logger.error("未找到登录按钮")
                self._dump_debug(self.page, "pwd_no_btn")
                return False
            login_btn.click()
            logger.info("已提交登录表单，等待登录结果...")

            if self._wait_login(40):
                logger.info("账号密码登录成功!")
                return True

            # 尝试读取错误提示
            err = ""
            try:
                alert = self.page.ele("#modal-alert", timeout=3)
                if alert:
                    err = alert.text.strip()
            except Exception:
                pass
            logger.error(f"账号密码登录失败 {('：' + err) if err else '(未出现登录态)'}")
            self._page_diag(self.page, "pwd_login_fail")
            self._dump_debug(self.page, "pwd_login_fail")
            return False
        except Exception as e:
            logger.error(f"浏览器登录异常: {e}")
            return False

    def login(self) -> bool:
        """
        登录编排。
        账号密码优先（Secrets 里永不过期，无需反复抓 Cookie）；
        Cookie 作为没配账号密码时的可选路线（cf_clearance 绑 IP 且短命，
        _t 之外的 Cookie 基本活不过一天，不适合长期自动化）。
        """
        login_res = False
        login_method = "-"

        if USERNAME and PASSWORD:
            if self.login_by_password():
                login_res, login_method = True, "password"
            elif COOKIES:
                logger.warning("账号密码登录失败，回退尝试 Cookie 登录...")
                if self.login_with_cookies(COOKIES):
                    login_res, login_method = True, "cookie"
        elif COOKIES:
            if self.login_with_cookies(COOKIES):
                login_res, login_method = True, "cookie"

        self.summary["login"] = login_res
        self.summary["login_method"] = login_method

        if login_res:
            try:
                # 从 /session/current.json 读真实用户名（比解析 DOM 更可靠）
                res = self.page.run_js(
                    "var x=new XMLHttpRequest();"
                    "x.open('GET','/session/current.json',false);"
                    "try{x.send()}catch(e){return null}"
                    "return x.status===200?x.responseText.substring(0,2000):null"
                )
                if res:
                    data = json.loads(res)
                    u = (data.get("current_user") or {}).get("username")
                    if u:
                        self.summary["username"] = u
            except Exception:
                pass
            self._sync_session_cookies()

        return login_res

    # ---------------- 论坛活跃 ----------------

    def click_topic(self) -> int:
        """随机浏览主题帖，返回成功访问的数量"""
        topic_list = self.page.ele("@id=list-area").eles(".:title")
        if not topic_list:
            logger.error("未找到主题帖")
            return 0
        sample_n = min(BROWSE_TOPIC_COUNT, len(topic_list))
        logger.info(f"发现 {len(topic_list)} 个主题帖，随机选择 {sample_n} 个")
        visited = 0
        for topic in random.sample(topic_list, sample_n):
            if self.click_one_topic(topic.attr("href")):
                visited += 1
        self.summary["topics_visited"] = visited
        return visited

    @retry_decorator()
    def click_one_topic(self, topic_url) -> bool:
        new_page = self.browser.new_tab()
        try:
            new_page.get(topic_url)
            self._wait_cf_clear(new_page, 30)
            if LIKE_ENABLED and random.random() < 0.3:
                self.click_like(new_page)
            self.browse_post(new_page)
            return True
        finally:
            try:
                new_page.close()
            except Exception:
                pass

    def browse_post(self, page):
        prev_url = None
        # 开始自动滚动，最多滚动10次
        for _ in range(10):
            # 随机滚动一段距离
            scroll_distance = random.randint(550, 650)  # 随机滚动 550-650 像素
            logger.info(f"向下滚动 {scroll_distance} 像素...")
            page.run_js(f"window.scrollBy(0, {scroll_distance})")
            logger.info(f"已加载页面: {page.url}")

            if random.random() < 0.03:  # 随机提前退出
                logger.success("随机退出浏览")
                break

            # 检查是否到达页面底部
            at_bottom = page.run_js(
                "window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            current_url = page.url
            if current_url != prev_url:
                prev_url = current_url
            elif at_bottom and prev_url == current_url:
                logger.success("已到达页面底部，退出浏览")
                break

            # 动态随机等待
            wait_time = random.uniform(2, 4)  # 随机等待 2-4 秒
            logger.info(f"等待 {wait_time:.2f} 秒...")
            time.sleep(wait_time)

    def click_like(self, page) -> bool:
        try:
            # 专门查找未点赞的按钮
            like_button = page.ele(".discourse-reactions-reaction-button")
            if like_button:
                logger.info("找到未点赞的帖子，准备点赞")
                like_button.click()
                logger.info("点赞成功")
                self.summary["likes"] += 1
                time.sleep(random.uniform(1, 2))
                return True
            logger.info("帖子可能已经点过赞了")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")
        return False

    # ---------------- LDC 积分站 ----------------

    def _try_click_auth_entry(self, page) -> bool:
        """在 credit 站登录页尝试点击登录/授权入口"""
        keywords = ("登录", "Login", "Sign in", "授权", "Authorize", "Allow")
        for kw in keywords:
            try:
                ele = page.ele(f"text:{kw}", timeout=1)
                if ele:
                    ele.click()
                    logger.info(f"已点击入口: {kw}")
                    time.sleep(3)
                    return True
            except Exception:
                continue
        return False

    def fetch_ldc_info(self) -> dict:
        """
        拉取 credit.linux.do 的 LDC 余额信息。
        优先：浏览器自动走 OAuth -> 提取 credit 会话 Cookie -> 调 API
        兜底：使用 LINUXDO_CREDIT_COOKIES 手动提供的 Cookie
        """
        info = {"enabled": LDC_ENABLED, "ok": False, "available_balance": "-", "community_balance": "-", "pending_balance": "-", "trust_level": "-", "username": "-"}
        if not LDC_ENABLED:
            info["note"] = "LDC_ENABLED 未开启"
            logger.info("LDC 查询未开启，跳过")
            return info

        credit_cookie_str = ""

        # 路线 A：浏览器 OAuth 自动登录
        try:
            if not self._goto_with_cf_retry(self.page, CREDIT_HOME_URL, wait_cf=45):
                logger.warning("credit.linux.do Cloudflare 挑战未通过，跳过浏览器自动登录")
            else:
                deadline = time.time() + 40
                on_credit = False
                while time.time() < deadline:
                    host = urlparse(self.page.url).netloc
                    if "credit.linux.do" in host:
                        # 已回到 credit 站，再确认不是挑战页
                        if not self._is_cf_challenge(self.page):
                            on_credit = True
                            break
                    else:
                        self._try_click_auth_entry(self.page)
                    time.sleep(2)

                if on_credit:
                    cookies = self._get_domain_cookies("credit.linux.do")
                    if cookies:
                        credit_cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
                        logger.info(f"已从浏览器获取 {len(cookies)} 个 credit.linux.do Cookie")
                else:
                    logger.warning("未能自动进入 credit.linux.do（OAuth 未完成）")
        except Exception as e:
            logger.warning(f"credit 站浏览器登录异常: {e}")

        # 路线 B：手动 Cookie 兜底
        if not credit_cookie_str and CREDIT_COOKIES:
            credit_cookie_str = CREDIT_COOKIES
            logger.info("使用手动提供的 LINUXDO_CREDIT_COOKIES")

        if not credit_cookie_str:
            info["note"] = "未获取到 credit.linux.do 会话"
            logger.warning("未获取到 credit 站会话，跳过 LDC 查询")
            return info

        # 调用接口（GET，无需 CSRF 头，已核对 credit 源码 csrfMiddleware 仅校验写方法）
        try:
            credit_session = requests.Session()
            credit_session.headers.update(
                {
                    "User-Agent": self.session.headers.get("User-Agent", "Mozilla/5.0"),
                    "Accept": "application/json",
                    "Referer": CREDIT_HOME_URL,
                }
            )
            for part in credit_cookie_str.split(";"):
                if "=" in part:
                    n, _, v = part.partition("=")
                    credit_session.cookies.set(n.strip(), v.strip(), domain="credit.linux.do")

            resp = credit_session.get(CREDIT_USER_INFO_URL, impersonate="chrome136", timeout=20)
            logger.info(f"credit user-info HTTP {resp.status_code}")
            if resp.status_code == 200:
                payload = resp.json()
                error_msg = payload.get("error_msg")
                data = payload.get("data") or {}
                if not error_msg and data:
                    info.update(
                        {
                            "ok": True,
                            "username": data.get("username", "-"),
                            "available_balance": str(data.get("available_balance", "-")),
                            "community_balance": str(data.get("community_balance", "-")),
                            "pending_balance": str(data.get("pending_balance", "-")),
                            "trust_level": TRUST_LEVEL_NAMES.get(
                                data.get("trust_level"), str(data.get("trust_level", "-"))
                            ),
                        }
                    )
                    logger.success(
                        f"LDC 查询成功: {info['username']} 可用 {info['available_balance']} / 社区 {info['community_balance']}"
                    )
                else:
                    info["note"] = error_msg or "接口返回空数据"
                    logger.warning(f"credit 接口返回异常: {error_msg}")
            elif resp.status_code in (401, 403):
                info["note"] = f"credit 会话无效 (HTTP {resp.status_code})"
                logger.warning(info["note"])
            else:
                info["note"] = f"HTTP {resp.status_code}"
                logger.warning(f"credit 接口异常: {resp.status_code}")
        except Exception as e:
            info["note"] = f"请求异常: {e}"
            logger.error(f"LDC 查询异常: {e}")

        self.summary["ldc"] = info
        return info

    # ---------------- Connect 信息 ----------------

    def print_connect_info(self) -> list:
        logger.info("获取连接信息")
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        }
        rows = []
        try:
            resp = self.session.get(CONNECT_URL, headers=headers, impersonate="chrome136")
            soup = BeautifulSoup(resp.text, "html.parser")
            for row in soup.select("table tr"):
                cells = row.select("td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip() if cells[1].text.strip() else "0"
                    requirement = cells[2].text.strip() if cells[2].text.strip() else "0"
                    rows.append([project, current, requirement])
        except Exception as e:
            logger.warning(f"Connect 信息获取失败: {e}")
            return []

        logger.info("--------------Connect Info-----------------")
        logger.info("\n" + tabulate(rows, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        self.summary["connect_rows"] = rows
        return rows

    # ---------------- 汇总与通知 ----------------

    def build_report(self) -> str:
        s = self.summary
        lines = []
        lines.append(f"👤 用户: {s.get('username') or '-'}")
        lines.append(
            f"🔑 登录: {'✅ 成功 (' + s.get('login_method', '-') + ')' if s.get('login') else '❌ 失败'}"
        )
        lines.append(f"📖 浏览主题: {s.get('topics_visited', 0)} 篇")
        if LIKE_ENABLED:
            lines.append(f"👍 点赞: {s.get('likes', 0)} 次")

        ldc = s.get("ldc") or {}
        if ldc.get("enabled"):
            if ldc.get("ok"):
                lines.append(
                    f"💰 LDC: 可用 {ldc.get('available_balance')} | 社区 {ldc.get('community_balance')} | 待结算 {ldc.get('pending_balance')} | 信任等级 {ldc.get('trust_level')}"
                )
            else:
                lines.append(f"💰 LDC: 查询失败 ({ldc.get('note', '未知原因')})")

        connect_rows = s.get("connect_rows") or []
        if connect_rows:
            lines.append("🔗 Connect:")
            for r in connect_rows[:8]:
                lines.append(f"   · {r[0]}: {r[1]} / {r[2]}")

        return "\n".join(lines)

    def send_notifications(self, browse_enabled):
        """发送签到通知"""
        status_msg = f"✅每日登录成功: {self.summary.get('username')}"
        if browse_enabled:
            status_msg += f" + 浏览 {self.summary.get('topics_visited', 0)} 篇"
        ldc = self.summary.get("ldc") or {}
        if ldc.get("ok"):
            status_msg += f" | LDC {ldc.get('available_balance')}"
        elif ldc.get("enabled"):
            status_msg += " | LDC 查询失败"

        detail = self.build_report()
        # 使用通知管理器发送所有通知
        self.notifier.send_all("LINUX DO", f"{status_msg}\n\n{detail}")

    def run(self):
        try:
            # 登录（Cookie 优先，账号密码兜底）
            login_res = self.login()
            if not login_res:
                logger.warning("登录验证失败，本次运行标记为失败")
                self._page_diag(self.page, "run_login_fail")
                self._dump_debug(self.page, "run_login_fail")

            if login_res and BROWSE_ENABLED:
                try:
                    visited = self.click_topic()  # 点击主题
                    if visited:
                        logger.info(f"完成浏览任务，共 {visited} 篇")
                    else:
                        logger.error("点击主题失败")
                except Exception as e:
                    logger.error(f"浏览任务异常: {e}")

            if login_res:
                self.fetch_ldc_info()  # LDC 积分站余额
                self.print_connect_info()  # 打印连接信息
            self.send_notifications(BROWSE_ENABLED)  # 发送通知
        finally:
            try:
                self.page.close()
            except Exception:
                pass
            try:
                self.browser.quit()
            except Exception:
                pass


if __name__ == "__main__":
    if not COOKIES and (not USERNAME or not PASSWORD):
        print("请设置 LINUXDO_COOKIES（Cookie 登录），或同时设置 USERNAME 和 PASSWORD（账号密码登录）")
        exit(1)
    browser = LinuxDoBrowser()
    browser.run()
