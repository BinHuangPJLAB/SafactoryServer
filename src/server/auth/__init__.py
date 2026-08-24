"""Bearer API-key authentication for HTTP requests."""

from server.auth.authenticator import BearerAuthenticator
from server.auth.loader import AuthConfigLoadError, load_auth_config

__all__ = ["AuthConfigLoadError", "BearerAuthenticator", "load_auth_config"]
