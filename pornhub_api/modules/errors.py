from base_api.modules import errors as base_errors


class PornhubAPIError(base_errors.ScraperException):
    """Base for Pornhub-specific errors; also catchable as a shared scraper error."""


class GifPendingReview(PornhubAPIError):
    pass


class VideoDisabled(PornhubAPIError):
    pass


class LoginFailed(PornhubAPIError):
    pass


class ClientAlreadyLogged(PornhubAPIError):
    pass


class NotFound(base_errors.NotFound, PornhubAPIError):
    pass


class NetworkError(base_errors.NetworkError, PornhubAPIError):
    pass


class BotDetection(base_errors.BotDetection, PornhubAPIError):
    pass


class ProxyError(base_errors.ProxyError, PornhubAPIError):
    pass


class UnknownNetworkError(base_errors.UnknownNetworkError, PornhubAPIError):
    pass


class DownloadFailed(base_errors.DownloadFailed, PornhubAPIError):
    pass
