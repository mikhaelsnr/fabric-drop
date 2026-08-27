"""Create a sample Fabric Drop incident in ServiceNow PROD.

The script is safe by default: without ``--send`` it only prints the payload.
Credentials are read from environment variables and are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


PROD_BASE_URL = os.getenv("SNOW_INSTANCE", "https://globe.service-now.com").rstrip("/")
PROD_INCIDENT_URL = f"{PROD_BASE_URL}/api/now/table/incident"
PROD_TOKEN_URL = f"{PROD_BASE_URL}/oauth_token.do"


def service_now_session() -> requests.Session:
    """Build the retry-enabled HTTP session used by the working integration."""
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PATCH"]),
    )
    adapter = HTTPAdapter(max_retries=retries, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"Accept": "application/json"})
    return session


def build_fabric_drop_payload(
    hostname: str,
    description: str,
    short_description: str | None = None,
) -> dict[str, str]:
    """Build the ServiceNow incident payload used for a Fabric Drop alarm."""
    return {
        "short_description": short_description
        or f"{hostname} Fabric Drop ACTIVE ALARM",
        "description": description,
        "category": "NTG",
        "subcategory": "Proactive",
        "u_service_impact": "502b49a44716ae54e9e31e1f116d43a9",
        "u_service_urgency": "f7cf85a0471aae54e9e31e1ff116d439e",
        "caller_id": "Python integration",
        "assignment_group": "NOC-WLN",
        "contact_type": "Integration",
        "u_application_service_incident": "NTG Application Service",
        "business_service": "NTG Services",
        "cmdb_ci": hostname,
        "u_subcategory": "Wireline",
        "u_for_broadcast": "true",
        "state": "2",
        "u_edo_sub_category": "Core",
        "u_main_site": hostname,
        "u_external_resource_impact": hostname,
        "u_service_affected": "eb6bf8a893a8ba90ca79705efaba1019",
    }


def get_oauth_token(
    session: requests.Session,
    client_id: str,
    client_secret: str,
    username: str,
    password: str,
    timeout: int = 30,
) -> str:
    """Obtain a ServiceNow OAuth access token using the password grant."""
    response = session.post(
        PROD_TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": client_id,
            "client_secret": client_secret,
            "username": username,
            "password": password,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"ServiceNow token request failed ({response.status_code}): "
            f"{response.text}"
        )
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("ServiceNow token response did not contain access_token")
    return token


def create_incident(
    payload: dict[str, str],
    *,
    client_id: str,
    client_secret: str,
    username: str,
    password: str,
    timeout: int = 30,
) -> dict[str, Any]:
    """Create an incident in the configured ServiceNow instance."""
    session = service_now_session()
    token = get_oauth_token(
        session, client_id, client_secret, username, password, timeout=timeout
    )
    response = session.post(
        PROD_INCIDENT_URL,
        json=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"ServiceNow incident creation failed ({response.status_code}): "
            f"{response.text}"
        )
    body = response.json()
    return body.get("result", body)


def credentials_from_environment() -> dict[str, str]:
    """Load required credentials without placing secrets in this source file."""
    variable_names = {
        "client_id": "SNOW_CLIENT_ID",
        "client_secret": "SNOW_CLIENT_SECRET",
        "username": "SNOW_USERNAME",
        "password": "SNOW_PASSWORD",
    }
    missing = [name for name in variable_names.values() if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing ServiceNow environment variable(s): " + ", ".join(missing)
        )
    return {key: os.environ[name] for key, name in variable_names.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a sample ServiceNow PROD ticket")
    parser.add_argument("--send", action="store_true", help="actually submit the ticket")
    parser.add_argument("--hostname", default="sample-bng-prod")
    parser.add_argument(
        "--description",
        default=(
            "PROD test incident generated by the Fabric Drop alarm integration. "
            "This is a test only; no live network alarm triggered it."
        ),
    )
    args = parser.parse_args()

    payload = build_fabric_drop_payload(args.hostname, args.description)
    if not args.send:
        print("DRY RUN - no ServiceNow ticket was created.")
        print(json.dumps(payload, indent=2))
        return

    result = create_incident(payload, **credentials_from_environment())
    print(
        json.dumps(
            {
                "number": result.get("number"),
                "sys_id": result.get("sys_id"),
                "state": result.get("state"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
