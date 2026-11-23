"""A single password in front of the whole site.

Set APP_PASSWORD to turn it on. Signing in sets a signed cookie that lasts a
year, so you rarely see the login page. Changing APP_PASSWORD signs out every
device. Run locally without APP_PASSWORD and there's no login at all; on
Vercel the site refuses to run without one, so it can never be public by
accident.
"""

import asyncio
import hashlib
import hmac
import html
import os
import time
from typing import Optional
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

COOKIE = "cincomil_session"
SESSION_SECONDS = 365 * 24 * 60 * 60
OPEN_PATHS = {"/login", "/logout"}


def password() -> Optional[str]:
    return os.environ.get("APP_PASSWORD") or None


def _key(secret: str) -> bytes:
    return hashlib.sha256(("cincomil-session:" + secret).encode()).digest()


def make_token(secret: str, now: Optional[float] = None) -> str:
    expires = int((time.time() if now is None else now) + SESSION_SECONDS)
    signature = hmac.new(_key(secret), str(expires).encode(), hashlib.sha256).hexdigest()
    return "%d.%s" % (expires, signature)


def token_is_valid(token: Optional[str], secret: str, now: Optional[float] = None) -> bool:
    if not token or "." not in token:
        return False
    expires, signature = token.split(".", 1)
    if not expires.isdigit() or int(expires) < (time.time() if now is None else now):
        return False
    expected = hmac.new(_key(secret), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _is_https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"


async def require_login(request: Request, call_next):
    """Middleware: every page and API call needs a signed-in cookie."""
    secret = password()
    if secret is None:
        if os.environ.get("VERCEL"):
            return Response(
                "Set APP_PASSWORD in the Vercel project settings to use this site.",
                status_code=503,
            )
        return await call_next(request)
    if request.url.path in OPEN_PATHS:
        return await call_next(request)
    if token_is_valid(request.cookies.get(COOKIE), secret):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Sign in first"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def login_page(error: str = "") -> HTMLResponse:
    message = '<p class="error">%s</p>' % html.escape(error) if error else ""
    return HTMLResponse(LOGIN_HTML.replace("{{message}}", message))


async def login(request: Request) -> Response:
    secret = password()
    if secret is None:
        return RedirectResponse("/", status_code=303)
    form = parse_qs((await request.body()).decode())
    attempt = (form.get("password") or [""])[0]
    if not hmac.compare_digest(attempt.encode(), secret.encode()):
        # Slows down guessing.
        await asyncio.sleep(1)
        return login_page("That's not the password.")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE,
        make_token(secret),
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
    )
    return response


def logout() -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hasta Cinco Mil</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
      background: #fafaf7;
      color: #2c2c2a;
      font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }
    form {
      width: 100%;
      max-width: 340px;
      padding: 32px 28px;
      background: #fff;
      border: 1px solid #e6e0d2;
      border-radius: 18px;
    }
    h1 { margin: 0 0 4px; font-size: 22px; }
    p.sub { margin: 0 0 22px; color: #888780; font-size: 13px; }
    label { display: block; margin-bottom: 6px; font-size: 13px; color: #5f5e5a; }
    input {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid #e6e0d2;
      border-radius: 10px;
      font: inherit;
    }
    input:focus { outline: none; border-color: #185fa5; box-shadow: 0 0 0 3px #e6f1fb; }
    button {
      width: 100%;
      margin-top: 14px;
      padding: 10px;
      border: 0;
      border-radius: 10px;
      background: #185fa5;
      color: #fff;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }
    .error { margin: 12px 0 0; color: #a32d2d; font-size: 13px; }
  </style>
</head>
<body>
  <form method="post" action="/login">
    <h1>Hasta Cinco Mil</h1>
    <p class="sub">Up to five thousand words.</p>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" autofocus required>
    <button type="submit">Sign in</button>
    {{message}}
  </form>
</body>
</html>
"""
