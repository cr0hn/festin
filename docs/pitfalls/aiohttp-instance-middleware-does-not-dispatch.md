---
tipo: pitfall
fecha: 2026-09-08
symptom-when-to-open: An aiohttp service where handlers 401/500 on every request or middleware seems ignored, with no visible error
---

# aiohttp 3.14 instance middleware silently doesn't dispatch new-style

## Symptom

A `@web.middleware`-decorated class instance registered in
`web.Application(middlewares=[...])` is never invoked: requests bypass auth
entirely, or the middleware raises `TypeError: object is not callable` — with
no startup warning. In FestIn, the JWT middleware silently stopped protecting
routes after an aiohttp upgrade.

## Cause

aiohttp 3.9+ dispatches new-style middleware only when the marker is visible
*on the instance*. `@web.middleware` decorates `__call__` of the class, but a
decorated *instance* loses the metadata that the dispatcher looks for.

`festin/service/serve.py` — the working pattern:

```python
def _wrap_middleware(instance):
    @web.middleware
    async def _mw(request, handler):
        # pre-processing
        return await instance.__call__(request, handler)
    return _mw
```

Also required on the class: `__middleware_version__ = 1` (see
`festin/service/auth.py`, `JWTMiddleware`).

## Contraste

Tested live against aiohttp 3.14: instance-registered middleware never
executed; the wrapped-function form executed on every request.

## How to avoid it

Never register middleware instances directly. Always wrap them with
`_wrap_middleware()` (or define middlewares as plain functions). Two real
bugs came from this: silent auth bypass and "Response object is not
callable".