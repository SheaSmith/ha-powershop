"""Sample API Client."""

from __future__ import annotations

import datetime
import socket
from typing import Any
from urllib.parse import urlencode

import aiohttp
import async_timeout
import oauthlib.oauth1
from oauthlib.oauth1 import SIGNATURE_TYPE_QUERY

from custom_components.powershop_nz.const import API_BASE_URL, API_CLIENT_VERSION, API_KEY, API_SECRET, API_DEVICE_TYPE, \
    API_DEVICE_NAME


class PowershopApiClientError(Exception):
    """Exception to indicate a general API error."""


class PowershopApiClientCommunicationError(
    PowershopApiClientError,
):
    """Exception to indicate a communication error."""


class PowershopApiClientAuthenticationError(
    PowershopApiClientError,
):
    """Exception to indicate an authentication error."""


def _verify_response_or_raise(response: aiohttp.ClientResponse) -> None:
    """Verify that the response is valid."""
    if response.status in (401, 403):
        msg = "Invalid credentials"
        raise PowershopApiClientAuthenticationError(
            msg,
        )
    response.raise_for_status()


class PowershopApiClient:
    """Sample API Client."""

    def __init__(
            self,
            username: str,
            password: str,
            session: aiohttp.ClientSession,
    ) -> None:
        """Sample API Client."""
        self._username = username
        self._password = password
        self._session = session
        self._token = None
        self._secret = None

    async def async_login(self) -> Any:
        """Login to PowerShop."""
        result = await self._api_wrapper(
            method="post",
            path="tokens",
            data={
                'api_key': API_KEY,
                'secret': API_SECRET,
                'email': self._username,
                'password': self._password,
                'device_type': API_DEVICE_TYPE,
                'device_name': API_DEVICE_NAME
            },
            oauth=False
        )

        self._token = result["data"]["token"]
        self._secret = result["data"]["secret"]

    async def async_get_accounts(self) -> Any:
        """Get the user's accounts from Powershop."""
        await self.async_login()

        return await self._api_wrapper(
            method="get",
            path="accounts"
        )

    async def async_get_hourly_usage(self, consumer_id) -> Any:
        """Get the usage for a given property from Powershop by consumer_id."""
        await self.async_login()

        to_datetime = datetime.datetime.combine(
            datetime.datetime.now().date() + datetime.timedelta(days=1),
            datetime.datetime.min.time(),
        )
        from_datetime = to_datetime - datetime.timedelta(days=30)

        return await self._api_wrapper(
            method="get",
            path=f"properties/{consumer_id}/usages",
            params={
                "from": from_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                "to": to_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

    async def async_get_data(self) -> Any:
        """Fetch accounts and per-property hourly usage for all properties."""
        accounts = await self.async_get_accounts()
        # Extract properties across all accounts
        properties: list[dict[str, Any]] = []
        usages: dict[str, Any] = {}

        data = accounts.get("data", {})
        for account in data.get("accounts", []):
            for prop in account.get("properties", []):
                consumer_id = str(prop.get("consumer_id"))
                if not consumer_id:
                    continue
                properties.append(
                    {
                        "consumer_id": consumer_id,
                        "name": prop.get("name"),
                        "connection_number": prop.get("connection_number"),
                        "account_number": account.get("number"),
                        "account_name": account.get("name"),
                    }
                )
        # Fetch usage per property (sequential to keep simple/minimal changes)
        for prop in properties:
            cid = prop["consumer_id"]
            try:
                usages[cid] = await self.async_get_hourly_usage(cid)
            except Exception as ex:  # Keep other properties even if one fails
                usages[cid] = {"error": str(ex)}

        return {
            "properties": properties,
            "usages": usages,
            "raw_accounts": accounts,
        }

    async def _api_wrapper(
            self,
            method: str,
            path: str,
            data: dict | None = None,
            params: dict | None = None,
            oauth: bool = True
    ) -> Any:
        actual_params = {
            'client_version': API_CLIENT_VERSION
        }

        if params is not None:
            actual_params = actual_params | params

        """Get information from the API."""
        try:
            async with async_timeout.timeout(10):
                # Build the base URI ensuring a single slash between base and path
                base = API_BASE_URL.rstrip("/")
                uri = f"{base}/{path}"
                # Append query parameters to the URI (do not also pass them to request, to keep OAuth signature valid)
                if actual_params:
                    uri = uri + "?" + urlencode(actual_params)

                headers = {
                    'Accept': 'application/json'
                }

                if oauth:
                    client = oauthlib.oauth1.Client(
                        client_key=API_KEY,
                        client_secret=API_SECRET,
                        resource_owner_key=self._token,
                        resource_owner_secret=self._secret,
                        signature_type=SIGNATURE_TYPE_QUERY
                    )

                    uri, headers, data = client.sign(uri, method, data, headers)

                response = await self._session.request(
                    method=method,
                    url=uri,
                    headers=headers,
                    data=data
                )
                _verify_response_or_raise(response)
                return await response.json()

        except TimeoutError as exception:
            msg = f"Timeout error fetching information - {exception}"
            raise PowershopApiClientCommunicationError(
                msg,
            ) from exception
        except (aiohttp.ClientError, socket.gaierror) as exception:
            msg = f"Error fetching information - {exception}"
            raise PowershopApiClientCommunicationError(
                msg,
            ) from exception
        except Exception as exception:  # pylint: disable=broad-except
            msg = f"Something really wrong happened! - {exception}"
            raise PowershopApiClientError(
                msg,
            ) from exception
