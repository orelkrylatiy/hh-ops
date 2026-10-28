from __future__ import annotations

import argparse
import asyncio
import logging
import re
import typing
from datetime import datetime
from http.cookiejar import Cookie
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

try:
    from playwright.async_api import async_playwright
except ImportError as exc:
    async_playwright = None
    _PLAYWRIGHT_IMPORT_ERROR = exc
else:
    _PLAYWRIGHT_IMPORT_ERROR = None

from ..main import BaseOperation
from ..utils.captcha_artifacts import save_captcha_image
from ..utils.opencode import solve_captcha_with_opencode
from ..utils.terminal import print_kitty_image, print_sixel_mage

if TYPE_CHECKING:
    from ..main import HHApplicantTool


HH_ANDROID_SCHEME = "hhandroid"

logger = logging.getLogger(__name__)


class Operation(BaseOperation):
    """Авторизация через Playwright"""

    __aliases__: list = ["authenticate", "auth", "login"]

    # Селекторы. Старые data-qa оставлены как fallback: hh.ru в 2026
    # перешёл на magritte-форму (телефон/почта раздельно).
    SEL_LOGIN_FORM = '[data-qa="account-login-form"]'
    SEL_LOGIN_INPUT = 'input[data-qa="login-input-username"]'
    SEL_PHONE_INPUT = (
        'input[data-qa="magritte-phone-input-national-number-input"]'
    )
    SEL_EMAIL_TAB = '[data-qa="credential-type-email"]'
    SEL_EMAIL_INPUT = 'input[data-qa="applicant-login-input-email"]'
    SEL_EXPAND_PASSWORD = (
        '[data-qa="expand-login-by-password"], '
        'button[data-qa="account-login-submit-by-password"]'
    )
    SEL_PASSWORD_INPUT = (
        'input[data-qa="login-input-password"], input[type="password"]'
    )
    SEL_CODE_CONTAINER = 'div[data-qa="account-login-code-input"]'
    SEL_PIN_CODE_INPUT = 'input[data-qa="magritte-pincode-input-field"]'
    SEL_CAPTCHA_IMAGE = 'img[data-qa="account-captcha-picture"]'
    SEL_CAPTCHA_INPUT = 'input[data-qa="account-captcha-input"]'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tool: HHApplicantTool | None = None
        self._args = None

    @property
    def is_headless(self) -> bool:
        return not self._args.no_headless and self.is_automated

    @property
    def is_automated(self) -> bool:
        return not self._args.manual

    @property
    def selector_timeout(self) -> int | None:
        return None if self.is_headless else 5000

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("username", nargs="?", help="Email или телефон")
        parser.add_argument("--password", "-p", help="Пароль для входа")
        parser.add_argument(
            "--no-headless",
            "-n",
            action="store_true",
            help="Показать окно браузера",
        )
        parser.add_argument(
            "-m", "--manual", action="store_true", help="Ручной режим ввода"
        )
        parser.add_argument(
            "-k",
            "--use-kitty",
            "--kitty",
            action="store_true",
            help="Вывод капчи в kitty",
        )
        parser.add_argument(
            "-s",
            "--use-sixel",
            "--sixel",
            action="store_true",
            help="Вывод капчи в sixel",
        )

    def run(self, tool: HHApplicantTool, args) -> int | None:
        self._tool = tool
        self._args = args
        try:
            asyncio.run(self._run())
        except (KeyboardInterrupt, asyncio.TimeoutError):
            logger.warning("Операция прервана пользователем или по таймауту")
            return 1
        return 0

    async def _run(self) -> None:
        if async_playwright is None:
            raise RuntimeError(
                "Не удалось импортировать Playwright"
                + (
                    f": {_PLAYWRIGHT_IMPORT_ERROR}"
                    if _PLAYWRIGHT_IMPORT_ERROR
                    else ""
                )
                + ".\nУстановите extra `playwright` и Chromium "
                "(`pip install 'hh-applicant-tool[playwright]'` и "
                "`hh-applicant-tool install`)."
            )

        args = self._args
        api_client = self._tool.api_client
        storage = self._tool.storage

        if self.is_automated:
            username = (
                args.username
                or storage.settings.get_value("auth.username")
                or (
                    await asyncio.to_thread(
                        input, "Введите email или телефон: "
                    )
                )
            ).strip()
            if not username:
                raise RuntimeError("Empty username")
            logger.debug(f"authenticate with: {username}")

        proxies = api_client.proxies
        proxy_url = proxies.get("https")
        chromium_args: list[str] = []
        if proxy_url:
            chromium_args.append(f"--proxy-server={proxy_url}")
            logger.debug(f"Используется прокси: {proxy_url}")

        if self.is_headless:
            logger.debug("Headless режим активен")

        async with async_playwright() as pw:
            logger.debug("Запуск браузера...")
            browser = await pw.chromium.launch(
                headless=self.is_headless, args=chromium_args
            )

            try:
                android_device = pw.devices["Galaxy A55"]
                context = await browser.new_context(**android_device)
                page = await context.new_page()

                code_future: asyncio.Future[str | None] = asyncio.Future()

                def handle_request(request):
                    url = request.url
                    if url.startswith(f"{HH_ANDROID_SCHEME}://"):
                        logger.info(f"Перехвачен OAuth redirect: {url}")
                        if not code_future.done():
                            sp = urlsplit(url)
                            code = parse_qs(sp.query).get("code", [None])[0]
                            code_future.set_result(code)

                page.on("request", handle_request)

                authorize_url = (
                    api_client.oauth_client.authorize_url  # + "&role=applicant"
                )
                logger.debug(f"Переход на страницу OAuth: {authorize_url}")
                await page.goto(
                    authorize_url,
                    timeout=60000,
                    wait_until="load",
                )

                if self.is_automated:
                    await self._fill_username(page, username)
                    logger.debug("Логин введен")

                    password = args.password or storage.settings.get_value(
                        "auth.password"
                    )
                    if password:
                        await self._direct_login(page, password)
                    else:
                        await self._onetime_code_login(page, code_future)
                else:
                    print(
                        "Откройте окно Chromium и войдите на hh.ru вручную.\n"
                        "После успешного входа утилита перехватит OAuth-код "
                        "и закроет браузер."
                    )

                logger.debug("Ожидание OAuth-кода...")
                auth_code = await asyncio.wait_for(
                    code_future, timeout=[None, 300.0][self.is_automated]
                )

                page.remove_listener("request", handle_request)

                logger.debug("Код получен, пробуем получить токен...")
                token = await asyncio.to_thread(
                    api_client.oauth_client.authenticate, auth_code
                )
                api_client.handle_access_token(token)

                print("Авторизация прошла успешно!")

                if self.is_automated:
                    storage.settings.set_value("auth.username", username)
                    if args.password:
                        storage.settings.set_value(
                            "auth.password", args.password
                        )

                storage.settings.set_value("auth.last_login", datetime.now())
                cookies = await context.cookies()
                self._set_session_cookies(cookies)

            finally:
                logger.debug("Закрытие браузера")
                await browser.close()

    @staticmethod
    def _national_phone(username: str) -> str:
        digits = re.sub(r"\D", "", username)
        if len(digits) == 11 and digits[0] in "78":
            return digits[1:]
        return digits

    async def _fill_username(self, page, username: str) -> None:
        await page.wait_for_selector(
            ", ".join(
                (
                    self.SEL_LOGIN_FORM,
                    self.SEL_LOGIN_INPUT,
                    self.SEL_PHONE_INPUT,
                    self.SEL_EMAIL_INPUT,
                )
            ),
            timeout=self.selector_timeout,
        )

        if "@" in username:
            email_tab = page.locator(self.SEL_EMAIL_TAB)
            if await email_tab.count():
                await email_tab.first.click(force=True)
            email_input = page.locator(self.SEL_EMAIL_INPUT)
            if await email_input.count():
                await email_input.first.fill(username)
                return

        phone_input = page.locator(self.SEL_PHONE_INPUT)
        if await phone_input.count() and "@" not in username:
            await phone_input.first.fill(self._national_phone(username))
            return

        await page.fill(self.SEL_LOGIN_INPUT, username)

    async def _direct_login(self, page, password: str) -> None:
        logger.info("Вход по паролю...")
        await page.locator(self.SEL_EXPAND_PASSWORD).first.click(force=True)
        await self._handle_captcha(page)
        await page.wait_for_selector(
            self.SEL_PASSWORD_INPUT, timeout=self.selector_timeout
        )
        await page.locator(self.SEL_PASSWORD_INPUT).first.fill(password)
        await page.locator(self.SEL_PASSWORD_INPUT).first.press("Enter")
        logger.debug("Форма с паролем отправлена")

    async def _onetime_code_login(self, page, code_future) -> None:
        logger.info("Вход по одноразовому коду...")
        login_field = page.locator(
            f"{self.SEL_EMAIL_INPUT}, {self.SEL_PHONE_INPUT}, {self.SEL_LOGIN_INPUT}"
        ).first
        await login_field.press("Enter")
        await self._handle_captcha(page)
        # hh.ru после капчи может думать заметно дольше 5с (медленные ответы),
        # поэтому код-инпут ждём фиксированную минуту, а не selector_timeout.
        # Если за это время прилетел OAuth redirect (вход завершился без
        # одноразового кода), ждать поле кода не нужно.
        try:
            await page.wait_for_selector(self.SEL_CODE_CONTAINER, timeout=60000)
        except asyncio.TimeoutError:
            if code_future.done():
                return
            raise

        print("Код был отправлен. Проверьте почту или SMS.")
        # Гонка: код могли ввести прямо в браузере — тогда OAuth redirect
        # уже перехвачен и читать код из терминала не нужно.
        input_task = asyncio.ensure_future(
            asyncio.to_thread(input, "Введите полученный код: ")
        )
        await asyncio.wait(
            (input_task, code_future), return_when=asyncio.FIRST_COMPLETED
        )
        if input_task.done():
            code = input_task.result().strip()
            if not code:
                raise RuntimeError("Код подтверждения не может быть пустым.")

            await page.fill(self.SEL_PIN_CODE_INPUT, code)
            await page.press(self.SEL_PIN_CODE_INPUT, "Enter")
            logger.debug("Форма с кодом отправлена")
        else:
            input_task.cancel()
            logger.debug("OAuth-код перехвачен до ввода кода из терминала")

    def _get_captcha_ai(self):
        try:
            return self._tool.get_captcha_ai()
        except Exception as ex:  # noqa: BLE001
            logger.info("AI-распознавание капчи недоступно: %s", ex)
            return None

    async def _solve_captcha_with_ai(self, page, captcha_element) -> bool:
        """Vision-AI-распознавание как в apply_vacancies._solve_captcha_async.

        До 5 попыток: неверный ответ => hh.ru рисует новую картинку.
        Успех — исчез инпут капчи (networkidle на hh.ru не наступает).
        """
        captcha_ai = self._get_captcha_ai()
        if captcha_ai is None:
            return False
        for attempt in range(5):
            captcha_text = ""
            try:
                img_bytes = await captcha_element.screenshot()
                image_path = save_captcha_image(
                    img_bytes,
                    flow="authorize",
                    attempt=attempt + 1,
                    profile_id=getattr(self._tool, "profile_id", None) or "default",
                )
                logger.info("Снимок капчи сохранён: %s", image_path)
                try:
                    captcha_text = await asyncio.to_thread(
                        solve_captcha_with_opencode, image_path.parent / "CURRENT.json"
                    )
                    if captcha_text:
                        logger.info("CAPTCHA распознана через OpenCode")
                except Exception as ex:  # noqa: BLE001
                    logger.warning("OpenCode CAPTCHA failed, using API fallback: %s", ex)
                    captcha_text = ""
                if not captcha_text:
                    captcha_text = await asyncio.to_thread(
                        captcha_ai.solve_captcha, img_bytes
                    )
            except Exception as ex:  # noqa: BLE001
                logger.warning("captcha AI failed (попытка %d): %s", attempt + 1, ex)
            if not captcha_text:
                logger.warning("капча не распознана (попытка %d)", attempt + 1)
                captcha_element = await page.wait_for_selector(
                    self.SEL_CAPTCHA_IMAGE, timeout=20000, state="visible"
                )
                continue
            logger.info(
                "Распознанный текст капчи (попытка %d): %s", attempt + 1, captcha_text
            )
            await page.fill(self.SEL_CAPTCHA_INPUT, captcha_text)
            await page.press(self.SEL_CAPTCHA_INPUT, "Enter")
            for _ in range(6):
                await page.wait_for_timeout(2500)
                if not await page.query_selector(self.SEL_CAPTCHA_INPUT):
                    logger.info("Капча решена AI")
                    return True
            logger.warning("капча не принята (попытка %d), пробую заново", attempt + 1)
            captcha_element = await page.wait_for_selector(
                self.SEL_CAPTCHA_IMAGE, timeout=20000, state="visible"
            )
        logger.error("AI не смог решить капчу за 5 попыток")
        return False

    async def _handle_captcha(self, page):
        try:
            captcha_element = await page.wait_for_selector(
                self.SEL_CAPTCHA_IMAGE,
                timeout=self.selector_timeout,
                state="visible",
            )
        except Exception:
            logger.debug("Капчи нет, продолжаем.")
            return

        if await self._solve_captcha_with_ai(page, captcha_element):
            return

        args = self._args
        if not (args.use_kitty or args.use_sixel):
            raise RuntimeError(
                "Требуется ввод капчи! Используйте --kitty или --sixel."
            )

        img_bytes = await captcha_element.screenshot()
        image_path = save_captcha_image(
            img_bytes,
            flow="authorize-manual",
            attempt=1,
            profile_id=getattr(self._tool, "profile_id", None) or "default",
        )
        logger.info("Снимок капчи сохранён: %s", image_path)
        print("\n[!] Требуется ввод капчи.")
        if args.use_kitty:
            print_kitty_image(img_bytes)
        elif args.use_sixel:
            print_sixel_mage(img_bytes)

        captcha_text = (
            await asyncio.to_thread(input, "Введите текст с картинки: ")
        ).strip()
        await page.fill(self.SEL_CAPTCHA_INPUT, captcha_text)
        await page.press(self.SEL_CAPTCHA_INPUT, "Enter")
        logger.debug("Капча отправлена")

    def _set_session_cookies(self, cookies: list[dict[str, typing.Any]]):
        for c in cookies:
            cookie = Cookie(
                version=0,
                name=c["name"],
                value=c["value"],
                port=None,
                port_specified=False,
                domain=c["domain"],
                domain_specified=True,
                domain_initial_dot=c["domain"].startswith("."),
                path=c["path"],
                path_specified=True,
                secure=c["secure"],
                expires=int(c.get("expires") or 0),
                discard=False,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": str(c.get("httpOnly", False))},
                rfc2109=False,
            )
            self._tool.session.cookies.set_cookie(cookie)
