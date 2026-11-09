"""Client for the Kryptex bridge running on the mining PC."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=15)


class KryptexError(Exception):
    """Base error."""


class KryptexConnectionError(KryptexError):
    """The bridge did not answer (PC off, wrong address, firewall)."""


class KryptexAuthError(KryptexError):
    """The API key was rejected."""


class KryptexBridgeClient:
    """Talks to the bridge's HTTP API."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, api_key: str) -> None:
        self._session = session
        self._base = f"http://{host}:{port}"
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def _request(self, method: str, path: str) -> dict[str, Any]:
        try:
            async with self._session.request(method, self._base + path, headers=self._headers, timeout=TIMEOUT) as response:
                if response.status == 401:
                    raise KryptexAuthError("The bridge rejected the API key.")
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise KryptexConnectionError(f"No valid answer from the bridge: {error}") from error
        if not isinstance(body, dict):
            raise KryptexConnectionError("The bridge returned an unexpected answer.")
        return body

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def status(self) -> dict[str, Any]:
        body = await self._request("GET", "/status")
        if not body.get("ok"):
            raise KryptexConnectionError(body.get("message", "The bridge reported an error."))
        return body

    async def start(self) -> dict[str, Any]:
        return await self._command("/start")

    async def stop(self) -> dict[str, Any]:
        return await self._command("/stop")

    async def pause(self) -> dict[str, Any]:
        return await self._command("/pause")

    async def resume(self) -> dict[str, Any]:
        return await self._command("/resume")

    async def _command(self, path: str) -> dict[str, Any]:
        body = await self._request("POST", path)
        if not body.get("ok"):
            raise KryptexError(body.get("message", "The command failed on the PC."))
        return body
