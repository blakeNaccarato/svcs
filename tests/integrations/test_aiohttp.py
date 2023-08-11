# SPDX-FileCopyrightText: 2023 Hynek Schlawack <hs@ox.cx>
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import attrs
import pytest

import svcs

from tests.fake_factories import async_int_factory
from tests.ifaces import Service


try:
    from aiohttp import ClientSession
    from aiohttp.web import (
        Application,
        AppRunner,
        Request,
        Response,
        TCPSite,
        json_response,
    )
    from yarl import URL
except ImportError:
    pytest.skip("AIOHTTP not installed", allow_module_level=True)


@attrs.define
class AppServer:
    """
    An HTTP server that serves an aiohttp app.
    """

    app: Application
    runner: AppRunner
    base_url: URL

    @classmethod
    async def start(cls, app: Application) -> AppServer:
        runner = AppRunner(app)
        await runner.setup()
        site = TCPSite(runner, "127.0.0.1", 0)
        await site.start()

        host, port = runner.addresses[0][:2]

        return cls(
            app=app,
            runner=runner,
            base_url=URL.build(scheme="http", host=host, port=port),
        )

    async def aclose(self):
        await self.runner.cleanup()


async def get(url: URL) -> tuple[Response, str]:
    async with ClientSession() as session, session.get(url) as resp:
        return resp, await resp.text()


async def view(request: Request) -> Response:
    return json_response(
        {
            "value": await svcs.aiohttp.aget(request, str),
            "factory": await svcs.aiohttp.svcs_from(request).aget(int),
        }
    )


async def health_view(request: Request) -> Response:
    return json_response(
        {p.name: (await p.aping()) for p in svcs.aiohttp.get_pings(request)}
    )


@pytest.fixture(name="app")
def _app(registry):
    return svcs.aiohttp.init_app(Application(), registry=registry)


@pytest.mark.asyncio()
class TestAIOHTTP:
    async def test_aclose_registry_ok(self, app):
        """
        aclose_registry closes the registry.
        """
        closed = False

        async def closer():
            nonlocal closed
            closed = True

        svcs.aiohttp.register_factory(
            app, Service, Service, on_registry_close=closer
        )

        await svcs.aiohttp.aclose_registry(app)

        assert closed

    async def test_aclose_registry_robust(self):
        """
        aclose_registry handles lack of of registry gracefully.
        """
        await svcs.aiohttp.aclose_registry(Application())

    async def test_registrations(self, app):
        """
        Registered values are returned.
        """
        app.router.add_get("/", view)

        svcs.aiohttp.register_value(app, str, "hello, world")
        svcs.aiohttp.register_factory(app, int, async_int_factory)

        server = await AppServer.start(app)
        resp, text = await get(server.base_url)

        await server.aclose()

        assert {"factory": 42, "value": "hello, world"} == json.loads(text)

    async def test_get_registry(self, registry, app):
        """
        get_registry returns the registry from the app that is passed.
        """
        assert registry is svcs.aiohttp.get_registry(app)

    async def test_get_pings(self, registry, container, app):
        """
        Our get_pings returns registered pings.
        """
        app.router.add_get("/", health_view)

        async def aping(_):
            ...

        svcs.aiohttp.register_factory(app, int, async_int_factory, ping=aping)

        server = await AppServer.start(app)
        resp, text = await get(server.base_url)

        assert {"builtins.int": None} == json.loads(text)

        await server.aclose()