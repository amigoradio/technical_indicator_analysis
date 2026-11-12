import requests

url = "https://openapi.longbridgeapp.com/oauth2/register"
payload = {
    "client_name": "My Longbridge App",
    "redirect_uris": ["http://localhost:60355/callback"],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"]
}

resp = requests.post(url, json=payload)
print(resp.text)
