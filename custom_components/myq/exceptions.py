class MyQError(Exception):
    pass


class MyQAuthenticationError(MyQError):
    pass


class MyQInvalidCredentialsError(MyQAuthenticationError):
    pass


class MyQInvalidMfaError(MyQAuthenticationError):
    pass


class MyQCloudflareChallengeError(MyQError):
    pass


class MyQApiError(MyQError):
    pass
