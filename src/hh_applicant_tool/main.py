from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import smtplib
import sqlite3
import sys
from collections.abc import Sequence
from functools import cached_property
from http.cookiejar import MozillaCookieJar
from importlib import import_module
from itertools import count
from os import getenv
from pathlib import Path
from pkgutil import iter_modules
from typing import Any, Callable, Iterable

import requests

from . import ai, api, utils
from .constants import (
    CONFIG_DIR,
    CONFIG_FILENAME,
    COOKIES_FILENAME,
    DATABASE_FILENAME,
    DESKTOP_USER_AGENT,
    LOG_FILENAME,
)
from .storage import StorageFacade
from .utils.cookiejar import HHOnlyCookieJar
from .utils.log import setup_logger
from .utils.mixins import MegaTool

logger = logging.getLogger(__package__)

OPERATIONS = "operations"
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class BaseOperation:
    def setup_parser(self, parser: argparse.ArgumentParser) -> None: ...

    def run(
        self,
        tool: HHApplicantTool,
        args: BaseNamespace,
    ) -> None | int:
        raise NotImplementedError()


class BaseNamespace(argparse.Namespace):
    profile_id: str
    config_dir: Path
    no_auto_auth: bool
    verbosity: int
    api_delay: float
    user_agent: str
    proxy_url: str
    openai_proxy_url: str
    json_output: bool
    operation_run: Callable[[HHApplicantTool, BaseNamespace], None | int] | None
    operation_name: str = ""


class HHApplicantTool(MegaTool):
    """Утилита для автоматизации действий соискателя на сайте hh.ru.

    Исходники и предложения: <https://github.com/s3rgeym/hh-applicant-tool>

    Группа поддержки: <https://t.me/hh_applicant_tool>
    """

    class ArgumentFormatter(
        argparse.ArgumentDefaultsHelpFormatter,
        argparse.RawDescriptionHelpFormatter,
    ):
        pass

    @classmethod
    def _create_parser(cls) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=cls.__doc__,
            formatter_class=cls.ArgumentFormatter,
        )
        parser.add_argument(
            "-v",
            "--verbosity",
            help="При использовании от одного и более раз увеличивает количество отладочной информации в выводе",  # noqa: E501
            action="count",
            default=0,
        )
        parser.add_argument(
            "-c",
            "--config-dir",
            "--config",
            help="Путь до директории с конфигом",
            type=Path,
            default=None,
        )
        parser.add_argument(
            "--profile-id",
            "--profile",
            help="Используемый профиль — подкаталог в --config-dir. Так же можно передать через переменную окружения HH_PROFILE_ID.",
        )
        parser.add_argument(
            "--no-auto-auth",
            action="store_true",
            help="Не запускать интерактивную авторизацию автоматически при отсутствии токена.",
        )
        parser.add_argument(
            "-d",
            "--api-delay",
            "--delay",
            type=float,
            help="Задержка между запросами к API HH по умолчанию",
        )
        parser.add_argument(
            "--user-agent",
            help="User-Agent для каждого запроса",
        )
        parser.add_argument(
            "--proxy-url",
            help="Прокси, используемый для запросов и авторизации",
        )
        parser.add_argument(
            "--openai-proxy",
            "--ai-proxy",
            dest="openai_proxy_url",
            help="Отдельный прокси, используемый только для OpenAI чата",
        )
        parser.add_argument(
            "--json",
            help="Вывод результата в формате JSON (для скриптов и агентов)",
            action="store_true",
        )
        subparsers = parser.add_subparsers(help="commands")
        package_dir = Path(__file__).resolve().parent / OPERATIONS
        for _, module_name, _ in iter_modules([str(package_dir)]):
            if module_name.startswith("_"):
                continue
            mod = import_module(f"{__package__}.{OPERATIONS}.{module_name}")
            op: BaseOperation = mod.Operation()
            kebab_name = module_name.replace("_", "-")
            op_parser = subparsers.add_parser(
                kebab_name,
                aliases=getattr(op, "__aliases__", []),
                description=op.__doc__,
                formatter_class=cls.ArgumentFormatter,
            )
            op_parser.set_defaults(operation_run=op.run, operation_name=kebab_name)
            op.setup_parser(op_parser)
        parser.set_defaults(operation_run=None)
        return parser

    def __init__(self):
        self._parser = self._create_parser()

    @staticmethod
    def _proxy_url_to_dict(proxy_url: str | None) -> dict[str, str]:
        if not proxy_url:
            return {}

        return {
            "http": proxy_url,
            "https": proxy_url,
        }

    def _get_proxies(self) -> dict[str, str]:
        proxy_url = self.proxy_url or self.config.get("proxy_url")

        if proxy_url:
            return self._proxy_url_to_dict(proxy_url)

        proxies = {}
        http_env = getenv("HTTP_PROXY") or getenv("http_proxy")
        https_env = getenv("HTTPS_PROXY") or getenv("https_proxy") or http_env

        if http_env:
            proxies["http"] = http_env
        if https_env:
            proxies["https"] = https_env

        return proxies

    def _get_openai_proxies(self) -> dict[str, str]:
        openai_config = self.config.get("openai", {})
        proxy_url = self.openai_proxy_url or openai_config.get("proxy_url")
        if proxy_url:
            return self._proxy_url_to_dict(proxy_url)
        return self._get_proxies()

    def _create_http_session(
        self,
        proxies: dict[str, str],
        *,
        log_label: str,
    ) -> requests.Session:
        session = requests.Session()

        if proxies:
            logger.info("Use proxies for %s: %r", log_label, proxies)
            session.proxies = proxies

        session.headers.update({"User-Agent": DESKTOP_USER_AGENT})
        return session

    @cached_property
    def session(self) -> requests.Session:
        session = self._create_http_session(
            self._get_proxies(),
            log_label="requests",
        )

        session.cookies = HHOnlyCookieJar(str(self.cookies_file))
        if self.cookies_file.exists():
            session.cookies.load(ignore_discard=True, ignore_expires=True)

        return session

    @cached_property
    def openai_session(self) -> requests.Session:
        return self._create_http_session(
            self._get_openai_proxies(),
            log_label="OpenAI requests",
        )

    @cached_property
    def config_path(self) -> Path:
        base_dir = self.config_dir or Path(getenv("CONFIG_DIR", CONFIG_DIR))
        profile_id = self.profile_id or getenv("HH_PROFILE_ID")

        if profile_id and profile_id != ".":
            profile_id = profile_id.strip()
            if not PROFILE_NAME_RE.fullmatch(profile_id):
                raise ValueError(
                    "Invalid profile name. Use letters, numbers, dot, dash or underscore."
                )

        return utils.resolve_profile_config_dir(base_dir, profile_id)

    @cached_property
    def config(self) -> utils.Config:
        return utils.Config(self.config_path / CONFIG_FILENAME)

    @cached_property
    def log_file(self) -> Path:
        return self.config_path / LOG_FILENAME

    @cached_property
    def cookies_file(self) -> Path:
        return self.config_path / COOKIES_FILENAME

    @cached_property
    def db_path(self) -> Path:
        return self.config_path / DATABASE_FILENAME

    @cached_property
    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        return conn

    @cached_property
    def storage(self) -> StorageFacade:
        return StorageFacade(self.db)

    @cached_property
    def api_client(self) -> api.client.ApiClient:
        config = self.config
        token = config.get("token", {})
        return api.client.ApiClient(
            client_id=config.get("client_id"),
            client_secret=config.get("client_secret"),
            access_token=token.get("access_token"),
            refresh_token=token.get("refresh_token"),
            access_expires_at=token.get("access_expires_at"),
            delay=self.api_delay or config.get("api_delay"),
            user_agent=self.user_agent or config.get("user_agent"),
            session=self.session,
        )

    def get_me(self) -> api.datatypes.User:
        return self.api_client.get("/me")

    def get_resumes(self) -> list[api.datatypes.Resume]:
        return self.api_client.get("/resumes/mine").get("items", [])

    def first_resume_id(self) -> str:
        resume = self.get_resumes()[0]
        return resume["id"]

    def get_blacklisted(self) -> list[str]:
        rv = []
        for page in count():
            r: api.datatypes.PaginatedItems[api.datatypes.EmployerShort] = (
                self.api_client.get("/employers/blacklisted", page=page)
            )
            rv += [item["id"] for item in r["items"]]
            if page + 1 >= r["pages"]:
                break
        return rv

    def get_negotiations(
        self, status: str = "active"
    ) -> Iterable[api.datatypes.Negotiation]:
        for page in count():
            r: dict[str, Any] = self.api_client.get(
                "/negotiations",
                page=page,
                per_page=100,
                status=status,
            )

            items = r.get("items", [])

            if not items:
                break

            yield from items

            if page + 1 >= r.get("pages", 0):
                break


    def _is_authenticated(self, config: dict[str, Any]) -> bool:
        account = config.get("account") or {}
        if not account:
            return False
        return any(value is not None for value in account.values())

    def parse_redirect_config(
        self,
        response: requests.Response,
        check_auth: bool = True,
    ) -> dict[str, Any]:
        if response.status_code != 200:
            raise RuntimeError(
                f"Unexpected HH response: {response.status_code} {response.url}"
            )

        try:
            raw_config = response.text.split(
                'id="HH-Lux-InitialState">'
            )[1].split("</template>")[0]
        except IndexError as exc:
            raise RuntimeError(
                f"HH-Lux-InitialState not found on {response.url}"
            ) from exc

        raw_config = html.unescape(raw_config)
        config = json.loads(raw_config)
        if not isinstance(config, dict) or "redirectConfig" not in config:
            raise ValueError("HH redirectConfig is missing from initial state")
        if check_auth and not self._is_authenticated(config):
            raise RuntimeError("HH web authorization has expired")
        return config

    def get_redirect_config(
        self,
        url: str,
        check_auth: bool = True,
    ) -> dict[str, Any]:
        return self.parse_redirect_config(self.session.get(url), check_auth)

    def save_token(self) -> bool:
        if self.api_client.access_token != self.config.get("token", {}).get(
            "access_token"
        ):
            self.config.save(token=self.api_client.get_access_token())
            return True
        return False

    def save_cookies(self) -> None:
        """Сохраняет текущие куки сессии в файл."""
        if isinstance(self.session.cookies, MozillaCookieJar):
            self.session.cookies.save(ignore_discard=True, ignore_expires=True)
            logger.debug("Cookies saved to %s", self.cookies_file)
        else:
            logger.warning(
                f"Сессионные куки имеют неправильный тип: {type(self.session.cookies)}"
            )

    def get_cover_letter_ai(self, system_prompt: str) -> ai.ChatOpenAI:
        return self._init_ai_client(system_prompt, purpose="cover_letter")

    def get_reply_ai(self, system_prompt: str) -> ai.ChatOpenAI:
        return self._init_ai_client(system_prompt, purpose="reply")

    def get_vacancy_filter_ai(self, system_prompt: str) -> ai.ChatOpenAI:
        return self._init_ai_client(system_prompt, purpose="vacancy_filter")

    def get_captcha_ai(self) -> ai.ChatOpenAI:
        return self._init_ai_client(system_prompt="Что написано на картинке?", purpose="captcha")

    def _init_ai_client(self, system_prompt: str, purpose: str) -> ai.ChatOpenAI:

        config_sections = {
            "cover_letter": "openai_cover_letter",
            "reply": "openai_reply",
            "vacancy_filter": "openai_vacancy_filter",
            "captcha": "openai_captcha",
        }
        
        if purpose not in config_sections:
            raise ValueError(
                f"Неизвестная цель AI: {purpose}. "
                f"Допустимые значения: {list(config_sections.keys())}"
            )
        
        config_section = config_sections[purpose]
        c = self.config.get(config_section, {})
        
        api_key = c.get("api_key")
        if not api_key:
            raise ValueError(
                f"API-ключ не задан. Укажите 'api_key' в секции '{config_section}' конфигурации."
            )

        base_url = c.get("base_url")
        if not base_url:
            raise ValueError(
                f"Параметр 'base_url' обязателен для AI-конфигурации в секции '{config_section}'. "
                "Примеры: OpenAI='https://api.openai.com/v1/chat/completions', "
                "Ollama='http://localhost:11434/v1/chat/completions', "
                "OpenRouter='https://openrouter.ai/api/v1/chat/completions'"
            )

        model = c.get("model")
        if not model:
            logger.warning(
                "Параметр 'model' не задан в секции '%s'. "
                "Большинство AI-провайдеров (OpenAI, OpenRouter) требуют указания модели. "
                "Примеры: 'gpt-4o-mini', 'gpt-3.5-turbo', 'openai/gpt-4'",
                config_section,
            )
    
        return ai.ChatOpenAI(
            api_key=api_key,
            model=model,
            temperature=c.get("temperature", 0.0),
            max_completion_tokens=c.get("max_completion_tokens", 1000),
            system_prompt=system_prompt,
            base_url=base_url,
            rate_limit=c.get("rate_limit", 40),
            timeout=c.get("timeout", 15.0),
            session=self.openai_session,
        )

    def _cookie_value(self, name: str) -> str | None:
        """Return one cookie value from the CookieJar by name."""
        return next(
            (cookie.value for cookie in self.session.cookies if cookie.name == name),
            None,
        )

    def _extract_xsrf_token(self, content: str) -> str:
        # hh.ru may HTML-escape embedded state and may expose multiple xsrfToken
        # values. The server validates the token matching the _xsrf cookie.
        content = html.unescape(content)
        tokens = re.findall(r',"xsrfToken":"([^"]+)"', content)
        if not tokens:
            raise ValueError("xsrf token not found")

        cookie_xsrf = self._cookie_value("_xsrf")
        if cookie_xsrf and cookie_xsrf in tokens:
            return cookie_xsrf
        return tokens[0]

    def _get_xsrf_token(self, url: str | None = None) -> str:
        """Return the XSRF token validated by hh.ru for this web session."""
        cookie_xsrf = self._cookie_value("_xsrf")
        if cookie_xsrf:
            return cookie_xsrf
        response = self.session.get(url or "https://hh.ru/")
        return self._extract_xsrf_token(response.text)

    @cached_property
    def xsrf_token(self) -> str:
        return self._get_xsrf_token()

    @property
    def is_logged_in(self) -> bool:
        """Проверяет авторизован ли пользователь через сайт."""
        return self.session.get("https://hh.ru/settings").status_code == 200

    @cached_property
    def smtp(self) -> smtplib.SMTP | smtplib.SMTP_SSL:
        conf = self.config.get("smtp", {})
        host = conf.get("host")
        port = conf.get("port")
        user = conf.get("user")
        password = conf.get("password")
        use_ssl = conf.get("ssl", False)

        if not host or not port:
            raise ValueError("SMTP host or port not configured")

        client_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        server = client_cls(host, port)

        if not use_ssl and conf.get("starttls", True):
            server.starttls()

        if user and password:
            server.login(user, password)

        return server

    def _ensure_authorized(self) -> bool:
        """Проверяет наличие токенов авторизации.

        Если токены отсутствуют, предлагает пользователю авторизоваться.
        Возвращает True если авторизация успешна, False иначе.
        """
        if self.api_client.access_token:
            logger.debug("Токен авторизации найден")
            return True

        if getattr(self, "no_auto_auth", False):
            logger.error("Требуется авторизация. Запустите: hh-applicant-tool authorize")
            return False

        logger.warning(
            "Токен авторизации не найден. "
            "Требуется авторизация через Playwright."
        )
        print("\n" + "="*60)
        print("⚠️  Требуется авторизация для работы приложения")
        print("="*60)
        print("\nПеред запуском основной операции необходимо авторизоваться.")
        print("Будет открыт браузер для ввода учетных данных HH.ru.\n")

        # Импортируем здесь, чтобы избежать циклических импортов
        from .operations import authorize

        auto_auth = input("Хотите авторизоваться сейчас? (y/n): ").strip().lower()
        if auto_auth != 'y':
            logger.error("Авторизация отменена пользователем")
            return False

        # Запускаем операцию авторизации
        auth_op = authorize.Operation()
        auth_args = argparse.Namespace(
            username=None,
            password=None,
            no_headless=False,
            manual=False,
            use_kitty=False,
            use_sixel=False,
            config_dir=self.config_dir,
            profile_id=self.profile_id,
            no_auto_auth=False,
            verbosity=self.verbosity,
            api_delay=self.api_delay,
            user_agent=self.user_agent,
            proxy_url=self.proxy_url,
            openai_proxy_url=self.openai_proxy_url,
        )

        try:
            result = auth_op.run(self, auth_args)
            if result != 0:
                logger.error("Авторизация завершилась с ошибкой")
                return False

            # Спасаем токен после успешной авторизации
            if self.save_token():
                logger.info("Токен сохранен успешно")
                return True
            else:
                logger.warning("Токен не был сохранен")
                return False

        except Exception as ex:
            logger.exception(f"Ошибка при авторизации: {ex}")
            return False

    def run(self, argv: Sequence[str] | None = None) -> None | int:
        args = self._parser.parse_args(argv, namespace=BaseNamespace())
        self._assign_args(args)

        # Создаем путь до конфига
        self.config_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        verbosity_level = max(
            logging.DEBUG,
            logging.WARNING - self.verbosity * 10,
        )

        setup_logger(logger, verbosity_level, self.log_file)

        logger.debug("Путь до профиля: %s", self.config_path)

        utils.setup_terminal()

        # Список операций, которые не требуют авторизации (без токена)
        no_auth_operations = {
            "authorize",
            "authenticate",
            "auth",
            "login",
            "config",
            "settings",
            "install",
            "uninstall",
            "migrate-db",
            "log",
        }

        # Операции, требующие авторизации, но без инициализации браузера
        fast_auth_check_operations = {
            "whoami",
            "id",
        }

        try:
            if self.operation_run:
                # Проверяем нужна ли авторизация для этой операции
                operation_name = getattr(args, "operation_name", "")
                needs_auth = operation_name not in no_auth_operations
                is_fast_check = operation_name in fast_auth_check_operations

                # Для операций требующих авторизации
                if needs_auth and not self.api_client.access_token:
                    # Для быстрых проверок (whoami) не инициируем браузер, просто ошибка
                    if is_fast_check:
                        logger.error(
                            "Требуется авторизация. Запустите: hh-applicant-tool authorize"
                        )
                        return 1

                    # Пытаемся обновить токен через refresh_token
                    if self.api_client.refresh_token:
                        logger.info("Пытаемся обновить токен...")
                        try:
                            self.api_client.refresh_access_token()
                            if self.save_token():
                                logger.info("Токен успешно обновлен")
                        except Exception as ex:
                            logger.debug(f"Не удалось обновить токен: {ex}")
                            # Если refresh_token тоже не сработал, требуем авторизацию
                            if self.no_auto_auth:
                                logger.error(
                                    "Операция отменена: требуется авторизация"
                                )
                                return 1
                            if not self._ensure_authorized():
                                logger.error("Операция отменена: требуется авторизация")
                                return 1
                    else:
                        # Требуем авторизацию
                        if not self._ensure_authorized():
                            logger.error("Операция отменена: требуется авторизация")
                            return 1

                try:
                    return self.operation_run(self, args)
                except KeyboardInterrupt:
                    logger.warning("Выполнение прервано пользователем!")
                except api.errors.CaptchaRequired as ex:
                    logger.error(f"Требуется ввод капчи: {ex.captcha_url}")
                except api.errors.InternalServerError:
                    logger.error(
                        "Сервер HH.RU не смог обработать запрос из-за высокой"
                        " нагрузки или по иной причине"
                    )
                except api.errors.Forbidden as ex:
                    request = ex.request
                    logger.error(
                        "Требуется авторизация: %s (HTTP %s %s -> %d)",
                        ex.message,
                        request.method,
                        request.url,
                        ex.status_code,
                    )
                except ValueError as ex:
                    logger.error(ex)
                except sqlite3.Error as ex:
                    logger.exception(ex)

                    script_name = sys.argv[0].split(os.sep)[-1]

                    logger.warning(
                        f"Возможно база данных повреждена, попробуйте выполнить команду:\n\n"  # noqa: E501
                        f"  {script_name} migrate-db"
                    )
                except Exception as e:
                    logger.exception(e)
                finally:
                    # Токен мог автоматически обновиться
                    if self.save_token():
                        logger.info("Токен был сохранен после обновления.")

                    try:
                        self.save_cookies()
                    except Exception as ex:
                        logger.error(f"Не удалось сохранить cookies: {ex}")
                return 1
            self._parser.print_help(file=sys.stderr)
            return 2
        finally:
            try:
                self._check_system()
            except Exception:
                pass
                # raise

    def _assign_args(self, args: BaseNamespace) -> None:
        for name, value in vars(args).items():
            setattr(self, name, value)


def main(argv: Sequence[str] | None = None) -> None | int:
    return HHApplicantTool().run(argv)
