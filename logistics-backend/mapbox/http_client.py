from __future__ import annotations

import logging
import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


logger = logging.getLogger("logistics.mapbox")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_mapbox_session() -> requests.Session:
    retries = Retry(
        total=int(os.getenv("MAPBOX_HTTP_RETRIES", "3")),
        connect=int(os.getenv("MAPBOX_HTTP_CONNECT_RETRIES", "3")),
        read=int(os.getenv("MAPBOX_HTTP_READ_RETRIES", "2")),
        backoff_factor=float(os.getenv("MAPBOX_HTTP_BACKOFF_SECONDS", "0.6")),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retries, pool_connections=8, pool_maxsize=16)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": os.getenv(
                "MAPBOX_USER_AGENT",
                "SBTU-Logistics/1.0 (+local-smart-logistics-demo)",
            )
        }
    )
    session.trust_env = _as_bool(os.getenv("MAPBOX_TRUST_ENV"), default=False)
    return session


def mapbox_get_json(
    url: str,
    *,
    params: dict[str, Any],
    service_name: str,
) -> dict[str, Any]:
    timeout = float(os.getenv("MAPBOX_HTTP_TIMEOUT_SECONDS", "20"))
    session = build_mapbox_session()
    verify_ssl = _as_bool(os.getenv("MAPBOX_SSL_VERIFY"), default=True)

    try:
        response = session.get(url, params=params, timeout=timeout, verify=verify_ssl)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("%s request failed for %s: %s", service_name, url, exc)
        raise
    finally:
        session.close()
