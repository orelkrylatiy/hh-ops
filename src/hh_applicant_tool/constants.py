from __future__ import annotations

from . import utils

# Paths & Files
CONFIG_DIR = utils.get_config_path() / "hh-applicant-tool"
CONFIG_FILENAME = "config.json"
LOG_FILENAME = "log.txt"
DATABASE_FILENAME = "data"
COOKIES_FILENAME = "cookies.txt"

# User Agent
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

# Admin Panel API Endpoints
ADMIN_API_PREFIX = "/api"
ADMIN_API_STATUS = f"{ADMIN_API_PREFIX}/status"
ADMIN_API_PROFILES = f"{ADMIN_API_PREFIX}/profiles"
ADMIN_API_STATS = f"{ADMIN_API_PREFIX}/stats"
ADMIN_API_NEGOTIATIONS = f"{ADMIN_API_PREFIX}/negotiations"
ADMIN_API_VACANCIES = f"{ADMIN_API_PREFIX}/vacancies"
ADMIN_API_SKIPPED = f"{ADMIN_API_PREFIX}/skipped"
ADMIN_API_EMPLOYERS = f"{ADMIN_API_PREFIX}/employers"
ADMIN_API_RESUMES = f"{ADMIN_API_PREFIX}/resumes"
ADMIN_API_CONFIG = f"{ADMIN_API_PREFIX}/config"
ADMIN_API_LOGS = f"{ADMIN_API_PREFIX}/logs"
ADMIN_API_USER = f"{ADMIN_API_PREFIX}/user"
ADMIN_API_AUTH_LOGOUT = f"{ADMIN_API_PREFIX}/auth/logout"
ADMIN_API_AUTH_REAUTHORIZE = f"{ADMIN_API_PREFIX}/auth/reauthorize"
ADMIN_API_GENERATE_LETTER = f"{ADMIN_API_PREFIX}/generate-letter"
ADMIN_API_RUN = f"{ADMIN_API_PREFIX}/run"
ADMIN_API_CANCEL = f"{ADMIN_API_PREFIX}/cancel"
ADMIN_API_OPERATIONS = f"{ADMIN_API_PREFIX}/operations"
ADMIN_API_OPERATION_STATUS = f"{ADMIN_API_PREFIX}/operation-status"

# Operations
ADMIN_OP_UPDATE_RESUMES = "update-resumes"
ADMIN_OP_APPLY_VACANCIES = "apply-vacancies"

# OpenAI Defaults
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"

# Admin Panel UI
ADMIN_DEFAULT_PROFILE = "default"
ADMIN_LOCALHOST = "127.0.0.1"
ADMIN_DEFAULT_PORT = 8000
ADMIN_OPERATION_TIMEOUT = 300  # seconds
ADMIN_LOG_OUTPUT_LIMIT = 5000  # chars for stdout
ADMIN_LOG_ERROR_LIMIT = 2000   # chars for stderr

# Response delays
RESPONSE_DELAY_MIN = 1.0  # seconds
RESPONSE_DELAY_MAX = 3.0  # seconds

# Masked config keys for security
MASKED_CONFIG_KEYS = {"access_token", "refresh_token", "password", "api_key"}
