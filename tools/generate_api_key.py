"""
Linux.do 用户 API Key 一次性生成工具（本地运行，只需做一次）

原理（Discourse 官方规范 https://meta.discourse.org/t/user-api-keys-specification/48536）：
1. 本地生成 RSA 密钥对，公钥放进授权 URL
2. 你在浏览器里打开该 URL 并点击"允许"（需已登录 linux.do）
3. Discourse 用你的公钥加密新生成的 API Key 并展示
4. 把密文粘贴回本脚本，用本地私钥解出 Key

Key 特性：长效（Discourse 规则：180 天不使用才过期），配合
LINUXDO_API_KEY 环境变量使用，之后签到不再需要 Cookie / 密码 / 浏览器。

用法：
    pip install pycryptodome
    python tools/generate_api_key.py
"""

from base64 import b64decode
import json
import secrets
from urllib.parse import urlencode

from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

SITE = "https://linux.do"
# read: 读帖/读列表；write: 上报阅读计时(POST)；session_info: 查询当前用户
SCOPES = "read,write,session_info"
APP_NAME = "linuxdo-checkin"


def main():
    key = RSA.generate(2048)
    public_key_pem = key.publickey().export_key().decode("ascii")

    query = urlencode(
        {
            "application_name": APP_NAME,
            "client_id": secrets.token_urlsafe(16),
            "scopes": SCOPES,
            "public_key": public_key_pem,
            "nonce": secrets.token_urlsafe(16),
        }
    )
    url = f"{SITE}/user-api-key/new?{query}"

    print("=" * 70)
    print("第 1 步：用已登录 linux.do 的浏览器打开下面的链接，并点击「允许」：")
    print()
    print(url)
    print()
    print("第 2 步：授权页面会显示一段加密文本（payload），完整复制粘贴到这里：")
    ciphertext = input("\npayload > ").strip().strip('"')

    cipher = PKCS1_v1_5.new(key)
    payload = json.loads(cipher.decrypt(b64decode(ciphertext), None).decode("utf-8"))
    print()
    print("=" * 70)
    print("你的 User API Key（请妥善保管，配置到 GitHub Secrets 的 LINUXDO_API_KEY）：")
    print()
    print(payload["key"])
    print("=" * 70)


if __name__ == "__main__":
    main()
