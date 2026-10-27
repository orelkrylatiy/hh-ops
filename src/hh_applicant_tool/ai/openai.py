import base64
import logging
import os
import re
import time
from dataclasses import KW_ONLY, dataclass, field
from email.utils import parsedate_to_datetime
from threading import Lock

import requests

from .base import AIError

logger = logging.getLogger(__package__)


def _normalize_captcha_text(value: str) -> str:
    """Extract the text shown by HH's visual CAPTCHA.

    HH serves both the older short ASCII tokens and warped Cyrillic phrases.
    Keep the latter intact (including spaces) instead of silently deleting all
    non-ASCII characters.
    """
    raw = (value or "").strip()
    if not raw:
        return ""

    raw = re.sub(r"^```(?:text)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()

    # Preserve the legacy short-token behaviour, including model responses
    # such as "The answer is xY7kQ2".
    compact = re.sub(r"[^A-Za-z0-9]", "", raw)
    if 4 <= len(compact) <= 8:
        return compact
    tokens = re.findall(r"[A-Za-z0-9]{4,8}", raw)
    if tokens and not re.search(r"[А-Яа-яЁё]", raw):
        return tokens[-1]

    # Vision models sometimes wrap the answer in quotes or introduce it with
    # a short label. Prefer that payload before applying conservative Unicode
    # filtering.
    quoted = re.findall(r"[«\"']([^»\"']+)[»\"']", raw)
    candidate = quoted[-1] if quoted else raw
    if ":" in candidate:
        prefix, payload = candidate.rsplit(":", 1)
        if re.search(r"(?i)(?:капч|captcha|ответ|текст|изображени|написан)", prefix):
            candidate = payload

    candidate = "".join(char if char.isalnum() or char in {" ", "-"} else " " for char in candidate)
    candidate = " ".join(candidate.split()).strip(" -")
    if 4 <= len(candidate) <= 64 and len(candidate.split()) <= 6:
        return candidate
    return ""


class OpenAIError(AIError):
    pass


_FRAMEWORK_CHAIN = None


def _framework_chain_provider():
    """Humanizer Framework's OpenCode-only provider for text generation."""
    global _FRAMEWORK_CHAIN
    if _FRAMEWORK_CHAIN is None:
        from .framework_provider import opencode_provider

        _FRAMEWORK_CHAIN = opencode_provider()
    return _FRAMEWORK_CHAIN


@dataclass
class ChatOpenAI:
    """Клиент для OpenAI-совместимых API (OpenAI, OpenRouter, Ollama и др.).

    Поддерживает:
    - rate limiting с настраиваемым лимитом запросов в минуту
    - автоматический retry при rate limit, временных 5xx и сетевых сбоях
    - генерацию текста (complete) и распознавание капчи (solve_captcha)
    """

    api_key: str

    _: KW_ONLY

    base_url: str
    system_prompt: str | None = None
    timeout: float = 15.0
    max_retries: int = 5
    temperature: float = 0.0
    max_completion_tokens: int = 1000
    model: str | None = None
    rate_limit: int = 40  # запросов в минуту, 0 = отключено
    verify_ssl: bool = True
    legacy_transport: bool = False

    session: requests.Session = field(default_factory=requests.Session)

    _previous_request_time: float = field(default=0.0, init=False)
    _lock: Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = Lock()

    def _default_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    @property
    def _min_request_interval(self) -> float:
        return 60.0 / self.rate_limit if self.rate_limit > 0 else 0.0

    def _request(self, payload: dict) -> requests.Response:
        """Выполняет POST-запрос с соблюдением минимального интервала между запросами."""
        with self._lock:
            if self._previous_request_time > 0:
                delay = self._min_request_interval - time.monotonic() + self._previous_request_time
                if delay > 0:
                    logger.debug("Wait %.2fs before OpenAI request", delay)
                    time.sleep(delay)

            try:
                return self.session.post(
                    self.base_url,
                    json=payload,
                    headers=self._default_headers(),
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                )
            finally:
                self._previous_request_time = time.monotonic()

    def _get_retry_delay(self, response: requests.Response, attempt: int) -> float:
        """Вычисляет задержку перед повторным запросом при 429."""
        min_interval = self._min_request_interval or 1.0
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), min_interval)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after).timestamp()
                    return max(retry_at - time.time(), min_interval)
                except (TypeError, ValueError, OverflowError):
                    pass
        return max(min_interval * (attempt + 1), 1.0)

    def _execute_with_retry(self, payload: dict) -> dict:
        """Отправляет payload и повторяет transient failures до max_retries раз.

        Returns:
            Распарсенный JSON-объект от API.

        Raises:
            OpenAIError: при сетевой ошибке, исчерпании попыток или невалидном ответе.
        """
        for attempt in range(self.max_retries + 1):
            try:
                response = self._request(payload)
            except requests.exceptions.RequestException as ex:
                if attempt >= self.max_retries:
                    raise OpenAIError(f"Network error: {ex}") from ex
                delay = max(self._min_request_interval * (attempt + 1), 1.0)
                logger.warning("AI network error, retry in %.2fs: %s", delay, ex)
                time.sleep(delay)
                continue

            is_transient = response.status_code in {408, 409, 425, 429} or (
                500 <= response.status_code < 600
            )
            if is_transient:
                if attempt >= self.max_retries:
                    try:
                        response.raise_for_status()
                    except requests.exceptions.RequestException as ex:
                        raise OpenAIError(f"Network error: {ex}") from ex
                delay = self._get_retry_delay(response, attempt)
                logger.warning(
                    "AI returned HTTP %d, retry in %.2fs",
                    response.status_code,
                    delay,
                )
                time.sleep(delay)
                continue

            try:
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.RequestException as ex:
                raise OpenAIError(f"Network error: {ex}") from ex
            except ValueError as ex:
                raise OpenAIError(f"Invalid JSON response: {ex}") from ex

            if not isinstance(data, dict):
                raise OpenAIError("Invalid JSON response: expected an object")

            provider_error = data.get("error")
            if provider_error:
                if isinstance(provider_error, dict):
                    message = provider_error.get("message") or str(provider_error)
                else:
                    message = str(provider_error)
                raise OpenAIError(message)

            return data

        raise OpenAIError("OpenAI request failed after retries")

    def complete(self, message: str) -> str:
        """Генерирует текст. Транспорт: цепочка humanizer-framework
        (OpenCode ChatGPT → GLM → OpenAI API, ключи в .env фреймворка).
        HH_AI_LEGACY=1 возвращает прежний прямой OpenAI-совместимый вызов.
        Vision-капча (solve_captcha) всегда ходит напрямую, как раньше.

        Args:
            message: пользовательский промпт.

        Returns:
            Текст ответа модели.
        """
        if not (self.legacy_transport or os.environ.get("HH_AI_LEGACY")):
            return self._complete_via_framework(message)

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": message})

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("AI запрос: %s", message)

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_completion_tokens,
            "stream": False,
        }

        data = self._execute_with_retry(payload)
        try:
            content = data["choices"][0]["message"]["content"]
            if content is None:
                return ""
            # reasoning-модели (GLM) иногда протаскивают блок размышления в content
            return re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
        except (KeyError, IndexError, TypeError) as ex:
            raise OpenAIError(f"Invalid response format: {ex}") from ex

    def _complete_via_framework(self, message: str) -> str:
        try:
            provider = _framework_chain_provider()
            messages = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            messages.append({"role": "user", "content": message})
            text = provider.generate(
                messages,
                temperature=self.temperature,
                max_tokens=self.max_completion_tokens,
            )
        except Exception as ex:
            raise OpenAIError(f"framework chain failed: {ex}") from ex
        # reasoning-модели иногда протаскивают блок размышления в content
        return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()

    def solve_captcha(self, image_data: bytes) -> str:
        """Распознаёт текст на изображении капчи через vision-модель.

        Args:
            image_data: бинарные данные изображения в формате PNG.

        Returns:
            Распознанный текст капчи.
        """
        image_base64 = base64.b64encode(image_data).decode("utf-8")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты должен распознать текст на изображении. "
                        "Верни ТОЛЬКО текст, без каких-либо объяснений или дополнительных символов."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_base64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": "Распознай текст на изображении. Верни только результат распознавания.",
                        },
                    ],
                },
            ],
            "temperature": 0.0,
            # У reasoning-моделей (GLM и т.п.) в лимит входит размышление:
            # берём конфиг, иначе reasoning съедает константу и content пустой.
            "max_completion_tokens": self.max_completion_tokens,
            "stream": False,
        }

        logger.debug("AI запрос на распознавание капчи: %d bytes", len(image_data))

        data = self._execute_with_retry(payload)
        try:
            message = data["choices"][0]["message"]
            captcha_text = (message.get("content") or "").strip()
            if not captcha_text:
                # reasoning-модель (GLM) может оставить ответ в reasoning_content
                reasoning = message.get("reasoning_content") or ""
                captcha_text = _normalize_captcha_text(reasoning)
                if captcha_text:
                    logger.debug("Капча восстановлена из reasoning_content: %s", captcha_text)
            # Models may wrap the answer in quotes, markdown, spaces or a sentence.
            # HH expects only the case-sensitive alphanumeric token.
            captcha_text = _normalize_captcha_text(captcha_text)
            logger.debug("Распознанный текст капчи: %s", captcha_text)
            return captcha_text
        except (KeyError, IndexError, TypeError) as ex:
            raise OpenAIError(f"Invalid response format: {ex}") from ex
