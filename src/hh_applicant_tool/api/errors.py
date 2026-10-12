from __future__ import annotations

from functools import cached_property
from typing import Any, Type

from requests import Request, Response
from requests.adapters import CaseInsensitiveDict

__all__ = (
    "BadResponse",
    "ApiError",
    "BadGateway",
    "BadRequest",
    "ClientError",
    "Forbidden",
    "InternalServerError",
    "CaptchaRequired",
    "LimitExceeded",
    "Redirect",
    "ResourceNotFound",
)


class BadResponse(Exception):
    pass


class ApiError(BadResponse):
    def __init__(self, response: Response, data: dict[str, Any]) -> None:
        self._response = response
        self._data = data

    @property
    def data(self) -> dict:
        return self._data

    @property
    def request(self) -> Request:
        return self._response.request

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def response_headers(self) -> CaseInsensitiveDict:
        return self._response.headers

    @property
    def message(self) -> str:
        if error_description := self._data.get("error_description"):
            return str(error_description)
        if description := self._data.get("description"):
            return str(description)

        errors = self._data.get("errors")
        if errors:
            return "An errors has occurred: " + "; ".join(
                self._format_error_item(item) for item in errors
            )

        return str(self._data)

    @staticmethod
    def _format_error_item(item: Any) -> str:
        if not isinstance(item, dict):
            return str(item)

        error_type = item.get("type")
        value = item.get("value")
        if error_type is None and value is None:
            return str(item)
        if error_type is None:
            return str(value)
        if value is None:
            return str(error_type)
        return f"{error_type}: {value}"

    def __str__(self) -> str:
        return self.message

    @staticmethod
    def has_error_value(value: Any, data: dict) -> bool:
        return any(
            item.get("value") == value
            for item in data.get("errors", [])
            if isinstance(item, dict)
        )

    @staticmethod
    def has_error_type(error_type: str, data: dict) -> bool:
        return any(
            item.get("type") == error_type
            for item in data.get("errors", [])
            if isinstance(item, dict)
        )

    @classmethod
    def raise_for_status(
        cls: Type[ApiError], response: Response, data: dict
    ) -> None:
        match response.status_code:
            case status if 300 <= status <= 308:
                raise Redirect(response, data)
            case 400:
                if cls.has_error_type("limit_exceeded", data) or cls.has_error_value(
                    "limit_exceeded",
                    data,
                ):
                    raise LimitExceeded(response, data)
                raise BadRequest(response, data)
            case 403:
                if cls.has_error_type("captcha_required", data) or cls.has_error_value(
                    "captcha_required",
                    data,
                ):
                    raise CaptchaRequired(response, data)
                raise Forbidden(response, data)
            case 404:
                raise ResourceNotFound(response, data)
            case status if 500 > status >= 400:
                raise ClientError(response, data)
            case 502:
                raise BadGateway(response, data)
            case status if status >= 500:
                raise InternalServerError(response, data)


class Redirect(ApiError):
    pass


class ClientError(ApiError):
    pass


class BadRequest(ClientError):
    pass


class LimitExceeded(ClientError):
    pass


class Forbidden(ClientError):
    @property
    def is_authentication_error(self) -> bool:
        """A forbidden chat action does not imply an invalid OAuth token."""
        return self.has_error_type("oauth", self.data) or self.has_error_value(
            "bad_authorization", self.data
        )


class CaptchaRequired(ClientError):
    @cached_property
    def captcha_url(self) -> str | None:
        return next(
            (
                item.get("captcha_url")
                for item in self._data.get("errors", [])
                if isinstance(item, dict)
                and (
                    item.get("type") == "captcha_required"
                    or item.get("value") == "captcha_required"
                )
            ),
            None,
        )

    @property
    def message(self) -> str:
        return f"Captcha required: {self.captcha_url}"


class ResourceNotFound(ClientError):
    pass


class InternalServerError(ApiError):
    pass


# По всей видимости, прокси возвращает, когда их бекенд на Java падает
# {'description': 'Bad Gateway', 'errors': [{'type': 'bad_gateway'}], 'request_id': '<md5 хеш>'}
class BadGateway(InternalServerError):
    pass
