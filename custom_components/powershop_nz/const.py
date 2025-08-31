"""Constants for powershop_nz."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "powershop_nz"
ATTRIBUTION = "Data provided by http://jsonplaceholder.typicode.com/"

API_BASE_URL = "https://secure.powershop.co.nz/external_api/v4/"
API_KEY = "7bd7cc52a071800c82fae35c8a063f09"
API_SECRET = "wWKjy1hRrlNmVVT60t6JvwnY4n8hfVcj"
API_DEVICE_TYPE = "Home Assistant"
API_DEVICE_NAME = "Home Assistant"
API_CLIENT_VERSION = '1.68.5'