from datetime import timedelta
from typing import Final

DOMAIN: Final = "myq"
MANUFACTURER: Final = "Chamberlain Group"

CONF_ACCESS_TOKEN: Final = "access_token"
CONF_EMAIL: Final = "email"
CONF_EXPIRES_AT: Final = "expires_at"
CONF_MFA_METHOD: Final = "mfa_method"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_TOKENS: Final = "tokens"

MFA_METHOD_EMAIL: Final = "email"
MFA_METHOD_SMS: Final = "sms"
DEFAULT_MFA_METHOD: Final = MFA_METHOD_EMAIL

IDENTITY_BASE_URL: Final = "https://partner-identity.myq-cloud.com"
ACCOUNTS_BASE_URL: Final = "https://accounts.myq-cloud.com"
DEVICES_BASE_URL: Final = "https://devices.myq-cloud.com"
GARAGE_DEVICES_BASE_URL: Final = "https://account-devices-gdo.myq-cloud.com"
TEND_API_BASE_URL: Final = "https://media.hubs.tend-us.tendplatform.com/cxs/api"
TEND_PARTNER_ID: Final = "myQ"
TEND_LOCALE: Final = "en-US"

OAUTH_CLIENT_ID: Final = "ANDROID_CGI_MYQ"
OAUTH_REDIRECT_URI: Final = "com.myqops://android"
OAUTH_SCOPE: Final = "MyQ_Residential offline_access"
APP_VERSION: Final = "5.243.1.73243"
USER_AGENT: Final = "sdk_gphone_x86/Android 11"
BROWSER_USER_AGENT: Final = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Mobile Safari/537.36"
)
BRAND_ID: Final = "1"

BROWSER_AUTH_TIMEOUT: Final = timedelta(minutes=10)
APP_CHECK_DATA: Final = bytes.fromhex(
    "26ce38db38df881debb4aa63859dfc9f73874291c4c7091ff400244f7203664472837397"
    "22c99b1cf1b9e43bd8c3e6d2209204f3c0980c1dae552a760b722c1d18ce0daf3bc9a331"
)

DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=30)
TOKEN_EXPIRY_MARGIN: Final = timedelta(minutes=1)
