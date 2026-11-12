from longbridge.openapi import Config, OAuthBuilder

# ========= 这里改成你的 client_id =========
client_id = ""
# ==========================================

oauth = OAuthBuilder(client_id).build(
    lambda url: print(f"Open this URL to authorize: {url}")
)
config = Config.from_oauth(oauth)