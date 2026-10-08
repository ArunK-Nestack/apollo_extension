class FreshsalesError(Exception):
    """Base exception for Freshsales agent."""


class FreshsalesAPIError(FreshsalesError):
    """Raised when Freshsales API returns an error response."""


class FreshsalesTransportError(FreshsalesError):
    """Raised when network / transport fails."""
