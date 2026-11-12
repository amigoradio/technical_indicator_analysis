import base64
import os

# ========= 这里改成你的 client_id =========
client_id = ""
# ==========================================

# 拼接Windows路径
home = os.path.expanduser("~")
token_file_path = os.path.join(home, ".longbridge", "openapi", "tokens", client_id)

if not os.path.exists(token_file_path):
    print(f"错误：文件不存在 -> {token_file_path}")
else:
    with open(token_file_path, "rb") as f:
        raw_data = f.read()
    b64_str = base64.b64encode(raw_data).decode("utf-8")
    print("===== 下面就是 LONGBRIDGE_OAUTH_TOKEN_CACHE_B64 的值，全部复制 =====")
    print(b64_str)
    print("===== 结束 =====")
