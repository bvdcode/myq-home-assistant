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

OAUTH_CLIENT_ID: Final = "ANDROID_CGI_MYQ"
OAUTH_REDIRECT_URI: Final = "com.myqops://android"
OAUTH_SCOPE: Final = "MyQ_Residential offline_access"
APP_VERSION: Final = "5.243.1.73243"
USER_AGENT: Final = "sdk_gphone_x86/Android 11"
BRAND_ID: Final = "1"

FIREBASE_PROJECT_ID: Final = "myq-transition-test"
FIREBASE_APP_ID: Final = "1:169499880894:android:120796f2b5e44ca7"
FIREBASE_API_KEY: Final = "AIzaSyDYwdJBRp6H3UhrCp5LGY8XTPJG7hTeCgw"
FIREBASE_DEBUG_TOKEN: Final = "25A02BB5-4064-4555-9414-F3449D5E5E75"
ANDROID_PACKAGE: Final = "com.chamberlain.android.liftmaster.myq"
ANDROID_CERT_SHA1: Final = "da2bda70ee8a9062d076babe65924caf9a8b98e9"

DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=30)
TOKEN_EXPIRY_MARGIN: Final = timedelta(minutes=1)
