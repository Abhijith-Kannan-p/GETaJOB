"""Per-IP rate limits (slowapi). One instance shared by all routers."""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def client_ip(request: Request) -> str:
    # Render/Vercel put the real client first in X-Forwarded-For
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else get_remote_address(request)


limiter = Limiter(key_func=client_ip)
