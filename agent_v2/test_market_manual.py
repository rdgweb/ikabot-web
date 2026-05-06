"""
Manual test: login as BlackShadow701, post a sell offer, and log everything.
Run inside agent container:
  docker compose exec agent python test_market_manual.py
"""
import json
import logging
import os
import sys

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

# ── patch requests to log all HTTP traffic ───────────────────────────────────
import requests
from requests import Session as _Session
_orig_send = _Session.send

def _logged_send(self, request, **kwargs):
    print(f"\n>>> HTTP {request.method} {request.url}")
    if request.body:
        body = request.body
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        print(f"    BODY: {body[:800]}")
    response = _orig_send(self, request, **kwargs)
    print(f"<<< {response.status_code} | {len(response.content)} bytes")
    # Show first 2000 chars of response
    try:
        text = response.text[:2000]
    except Exception:
        text = "<binary>"
    print(f"    RESP: {text}\n")
    return response

_Session.send = _logged_send
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, "/app")

from core.hub_client import HubClient
from game_client.client import GameClient


def main():
    hub_url = os.environ.get("HUB_URL", "http://hub:8000")
    token = os.environ.get("AGENT_TOKEN", "")

    hub = HubClient(hub_url=hub_url, agent_token=token, node_id="local-dev")

    # Get config to find BlackShadow701 credentials
    config = hub.get_config()
    accounts = config.get("accounts", [])

    creds = None
    ga_id = None
    for acc in accounts:
        for ga in acc.get("game_accounts", []):
            if ga.get("name") == "BlackShadow701":
                creds = {
                    "server": ga.get("server_id"),
                    "email": acc.get("email"),
                    "password": acc.get("password"),
                    "gf_token": acc.get("gf_token", ""),
                    "lobby_account_id": ga.get("lobby_account_id"),
                }
                ga_id = ga.get("id")
                print(f"Found creds: server={creds['server']} email={creds['email'][:6]}... ga_id={ga_id}")
                break
        if creds:
            break

    if not creds:
        print("ERROR: Could not find credentials for BlackShadow701")
        return

    # Login
    print("\n=== LOGIN ===")
    client = GameClient(account_id=ga_id, hub=hub, proxy_url=None)
    client.login(
        server_id=creds["server"],
        email=creds["email"],
        password=creds["password"],
        gf_token=creds.get("gf_token"),
        lobby_account_id=creds.get("lobby_account_id"),
    )
    print(f"server_url={client._server_url}")
    print(f"action_request={client._action_request}")

    # Test changeCity first
    print("\n=== CHANGE CITY (31018 = Valhala) ===")
    from game_client.constants import ActionID
    try:
        result = client._ajax(ActionID.CHANGE_CITY, {
            "action": "HeaderAction",
            "function": "changeCity",
            "cityId": 31018,
            "backgroundView": "city",
            "currentCityId": 31018,
            "templateView": "city",
            "actionRequest": client._action_request,
            "ajax": "1",
        })
        print("changeCity result:", json.dumps(result, default=str)[:500])
    except Exception as e:
        print(f"changeCity ERROR: {e}")

    print(f"\naction_request after changeCity: {client._action_request}")

    # Test updateOffers (small amount = 1 unit for testing)
    print("\n=== UPDATE OFFERS (sell 1 wood @12) ===")
    try:
        result = client._ajax(ActionID.MARKETPLACE_UPDATE_OFFERS, {
            "action": "CityScreen",
            "function": "updateOffers",
            "cityId": 31018,
            "position": 7,
            "resourceTradeType": "444",
            "resource": "1",
            "resourcePrice": "12",
            "tradegood1TradeType": "444",
            "tradegood1": "0",
            "tradegood1Price": "11",
            "tradegood2TradeType": "444",
            "tradegood2": "0",
            "tradegood2Price": "12",
            "tradegood3TradeType": "444",
            "tradegood3": "0",
            "tradegood3Price": "17",
            "tradegood4TradeType": "444",
            "tradegood4": "0",
            "tradegood4Price": "5",
            "backgroundView": "city",
            "currentCityId": 31018,
            "templateView": "branchOfficeOwnOffers",
            "currentTab": "tab_branchOfficeOwnOffers",
            "actionRequest": client._action_request,
            "ajax": "1",
        })
        print("updateOffers result:", json.dumps(result, default=str)[:1000])
    except Exception as e:
        print(f"updateOffers ERROR: {e}")

    # Now check what's on the branch office
    print("\n=== READ BRANCH OFFICE LISTING (own offers) ===")
    try:
        resp = client._request("POST", client._server_url, data={
            "action": "CityScreen",
            "function": "index",
            "cityId": 31018,
            "position": 7,
            "currentCityId": 31018,
            "backgroundView": "city",
            "templateView": "branchOfficeOwnOffers",
            "currentTab": "tab_branchOfficeOwnOffers",
            "actionRequest": client._action_request,
            "ajax": "1",
        })
        print("BranchOffice own offers response:")
        text = resp.text
        # Look for offer amounts
        import re
        amounts = re.findall(r'"amount".*?(\d+)', text[:3000])
        print("Amounts found:", amounts[:10])
        # Also print raw if small
        if len(text) < 3000:
            print("RAW:", text)
    except Exception as e:
        print(f"Read BO ERROR: {e}")


if __name__ == "__main__":
    main()
