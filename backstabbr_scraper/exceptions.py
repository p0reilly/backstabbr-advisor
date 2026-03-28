class BackstabbrScraperError(Exception):
    pass


class AuthenticationError(BackstabbrScraperError):
    pass


class ParseError(BackstabbrScraperError):
    pass


class UnknownProvinceError(ParseError):
    pass


class CoastAmbiguityError(ParseError):
    pass


class PressUnavailableError(BackstabbrScraperError):
    """Raised when the press endpoint returns 404 (gunboat game or press disabled)."""
