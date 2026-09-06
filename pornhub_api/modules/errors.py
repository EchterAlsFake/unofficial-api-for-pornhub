class PornhubAPIError(Exception):
    def __init__(self, msg: str = ""):
        super().__init__(msg)
        self.msg = msg


class GifPendingReview(PornhubAPIError):
    pass


class VideoDisabled(PornhubAPIError):
    pass


class LoginFailed(PornhubAPIError):
    pass


class ClientAlreadyLogged(PornhubAPIError):
    pass


class NotFound(PornhubAPIError):
    pass


class NetworkError(PornhubAPIError):
    pass


class BotDetection(PornhubAPIError):
    pass


class ProxyError(PornhubAPIError):
    pass


class UnknownNetworkError(PornhubAPIError):
    pass


class DownloadFailed(PornhubAPIError):
    pass