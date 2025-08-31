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
from bs4 import BeautifulSoup

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
    """Client for Powershop API and CSV usage report."""

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
        self._web_logged_in = False

    async def async_get_rates(self, consumer_id: str) -> Any:
        """Get the rates for a given property from Powershop by consumer_id."""
        await self.async_login()
        return await self._api_wrapper(
            method="get",
            path=f"properties/{consumer_id}/rates",
        )

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

    async def _web_get_authenticity_token(self) -> str | None:
        """Fetch login page and extract authenticity/CSRF token.
        Returns token string or None if not found.
        """
        try:
            async with async_timeout.timeout(20):
                resp = await self._session.get("https://secure.powershop.co.nz/")
                _verify_response_or_raise(resp)
                html = await resp.text()
        except Exception:
            return None
        try:
            soup = BeautifulSoup(html, "html.parser")
            inp = soup.find("input", {"name": "authenticity_token"}) or soup.find("input", {"name": "csrf-token"})
            if inp and inp.get("value"):
                return inp.get("value")
        except Exception:
            return None
        return None

    async def _web_login(self) -> bool:
        """Login to secure.powershop.co.nz to enable CSV download, Meridian-style."""
        if self._web_logged_in:
            return True
        token = await self._web_get_authenticity_token()
        form = {
            "email": self._username,
            "password": self._password,
            "commit": "Login",
        }
        if token:
            form["authenticity_token"] = token
        try:
            async with async_timeout.timeout(20):
                resp = await self._session.post(
                    "https://secure.powershop.co.nz/customer/login",
                    data=form,
                    headers={"Referer": "https://secure.powershop.co.nz/"},
                )
                # Powershop redirects or returns 200 on success; treat 2xx as success
                if 200 <= resp.status < 300:
                    self._web_logged_in = True
                    return True
        except Exception:
            pass
        self._web_logged_in = False
        return False

    async def async_get_usage_report(self, date_from: datetime.date, date_to: datetime.date) -> Any:
        """Download and parse the CSV usage report from the secure site.

        Returns a list of rows, each row containing:
        {
            "icp": str,
            "meter_number": str,
            "element": str,  # e.g. 'Controlled', 'Uncontrolled'
            "date": datetime.date,
            "values_kwh": list[float],  # 48 half-hour kWh values
        }
        """
        # Ensure we have a logged-in web session for CSV
        if not await self._web_login():
            raise PowershopApiClientAuthenticationError("Could not login to secure site for CSV report")
        # Format dd/mm/YYYY as required by the endpoint
        q_from = date_from.strftime("%d/%m/%Y")
        q_to = date_to.strftime("%d/%m/%Y")
        url = (
            "https://secure.powershop.co.nz/usage_report/download?" +
            urlencode({"from": q_from, "to": q_to, "download": "download"})
        )
        try:
            async with async_timeout.timeout(20):
                resp = await self._session.get(
                    url,
                    headers={"Accept": "text/csv, text/tab-separated-values, */*", "Referer": "https://secure.powershop.co.nz/"},
                    allow_redirects=True,
                )
                _verify_response_or_raise(resp)
                text = await resp.text()
        except Exception as exception:
            raise PowershopApiClientCommunicationError(f"Error downloading usage report - {exception}") from exception

        # Powershop exports as tab-separated values with a header row.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return []
        # First row is header; subsequent rows are data
        header = lines[0].split("\t")
        # Find indices
        try:
            idx_icp = header.index("ICP")
            idx_meter = header.index("Meter number")
            idx_element = header.index("Meter element")
            idx_date = header.index("Date")
            first_interval_idx = idx_date + 1
        except ValueError:
            # Unexpected header; attempt with comma
            header = lines[0].split(",")
            idx_icp = header.index("ICP")
            idx_meter = header.index("Meter number")
            idx_element = header.index("Meter element")
            idx_date = header.index("Date")
            first_interval_idx = idx_date + 1

        rows: list[dict[str, Any]] = []
        for ln in lines[1:]:
            parts = ln.split("\t") if "\t" in ln else ln.split(",")
            if len(parts) <= first_interval_idx:
                continue
            icp = parts[idx_icp].strip()
            meter = parts[idx_meter].strip()
            element = parts[idx_element].strip()
            dstr = parts[idx_date].strip()
            # Accept formats like 5/08/2025
            try:
                d = datetime.datetime.strptime(dstr, "%d/%m/%Y").date()
            except Exception:
                # Try alternative
                d = datetime.datetime.strptime(dstr, "%Y-%m-%d").date()
            values_kwh: list[float] = []
            for v in parts[first_interval_idx:]:
                v = v.strip()
                if not v:
                    continue
                try:
                    values_kwh.append(float(v))
                except ValueError:
                    # Non-numeric; skip
                    values_kwh.append(0.0)
            # Ensure exactly 48 slots
            if len(values_kwh) < 48:
                values_kwh.extend([0.0] * (48 - len(values_kwh)))
            elif len(values_kwh) > 48:
                values_kwh = values_kwh[:48]
            rows.append({
                "icp": icp,
                "meter_number": meter,
                "element": element,
                "date": d,
                "values_kwh": values_kwh,
            })
        return rows

    async def async_get_data(self) -> Any:
        """Fetch accounts and per-property hourly usage for all properties, plus CSV usage per meter element.

        The CSV download at /usage_report/download contains half-hour kWh figures per
        meter element. We parse it and expose a structured mapping so the sensor
        platform can create separate entities for each meter element.
        """
        accounts = await self.async_get_accounts()
        # Extract properties across all accounts
        properties: list[dict[str, Any]] = []
        usages: dict[str, Any] = {}
        rates_summary: dict[str, Any] = {}

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
        # Fetch usage and special incl rate per property (sequential minimal changes)
        # Determine current month label like 'Aug'
        current_month_label = datetime.datetime.now().strftime("%b")
        for prop in properties:
            cid = prop["consumer_id"]
            # Usage
            try:
                usages[cid] = await self.async_get_hourly_usage(cid)
            except Exception as ex:  # Keep other properties even if one fails
                usages[cid] = {"error": str(ex)}
            # Rates
            special_incl_dollars = None
            try:
                rates_payload = await self.async_get_rates(cid)
                # Navigate to special rates
                rates_data = (rates_payload or {}).get("data", {}).get("rates", {})
                specials = rates_data.get("special", []) or []
                # Find first entry with a meter_number
                first_with_meter = None
                for item in specials:
                    if item.get("meter_number"):
                        first_with_meter = item
                        break
                if first_with_meter:
                    # Find rate for current month
                    for rate in first_with_meter.get("rates", []) or []:
                        if rate.get("month") == current_month_label:
                            incl_list = rate.get("incl", []) or []
                            if incl_list:
                                # Convert cents to dollars; spec says use the first value
                                special_incl_dollars = float(incl_list[0]) / 100.0
                            break
            except Exception as ex:
                # Keep going; store error info if needed later
                special_incl_dollars = None
            rates_summary[cid] = {
                "month_label": current_month_label,
                "special_incl_dollars_current_month": special_incl_dollars,
            }

        # Attempt to fetch the CSV usage report for the past 30 days, then map to properties by ICP/connection_number
        try:
            end_date = datetime.datetime.now().date()
            start_date = end_date - datetime.timedelta(days=30)
            csv_rows = await self.async_get_usage_report(start_date, end_date)
        except Exception as ex:
            csv_rows = []

        # Build mapping icp -> { element_name -> {date->values_kwh}}
        icp_map: dict[str, dict[str, dict[datetime.date, list[float]]]] = {}
        for row in csv_rows:
            icp = row.get("icp")
            elem = row.get("element")
            d = row.get("date")
            vals = row.get("values_kwh") or []
            if not icp or not elem or not d:
                continue
            icp_map.setdefault(icp, {}).setdefault(elem, {})[d] = list(vals)

        # Convert to Home Assistant-friendly structure: per property -> per element -> usages [{date, usage[Wh]}]
        elements_by_property: dict[str, dict[str, Any]] = {}
        for prop in properties:
            cid = prop["consumer_id"]
            icp = prop.get("connection_number")
            element_map = icp_map.get(icp, {}) if icp else {}
            out: dict[str, Any] = {}
            for elem_name, date_map in element_map.items():
                usages_list = []
                for d, values_kwh in sorted(date_map.items()):
                    # Convert kWh to Wh values to match the rest of the code expectations
                    usage_wh = [round(v * 1000.0, 3) for v in values_kwh]
                    usages_list.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "iso8601_date": d.strftime("%Y-%m-%d"),
                        "usage": usage_wh,
                    })
                out[elem_name] = {"usages": usages_list}
            elements_by_property[cid] = out

        return {
            "properties": properties,
            "usages": usages,
            "rates": rates_summary,
            "elements": elements_by_property,
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
