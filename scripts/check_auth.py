"""验证凭证能登录：不发帖，不打印任何密钥，只打印账号身份。"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post import CREDENTIALS_FILE, REQUIRED_KEYS, BlueskyClient, oauth1_header  # noqa: E402 — 先把脚本目录放进 path

X_ME_URL = "https://api.x.com/2/users/me"


def read_filled_keys():
    filled = set()
    for line in CREDENTIALS_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            if value.strip():
                filled.add(key.strip())
    return filled


def check_bluesky(creds):
    print("Bluesky 登录成功，DID:", BlueskyClient(creds).did)


def check_x(creds):
    request = urllib.request.Request(X_ME_URL, headers={"Authorization": oauth1_header("GET", X_ME_URL, creds)})
    with urllib.request.urlopen(request, timeout=30) as response:
        me = json.loads(response.read())["data"]
    print(f"X 登录成功：@{me['username']}（id {me['id']}）")


def main():
    filled = read_filled_keys()
    for platform, keys in REQUIRED_KEYS.items():
        missing = [k for k in keys if k not in filled]
        if missing:
            print(f"{platform}: 缺 {', '.join(missing)}，跳过")
            continue
        creds = {}
        for line in CREDENTIALS_FILE.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                creds[key.strip()] = value.strip()
        try:
            (check_bluesky if platform == "bluesky" else check_x)(creds)
        except urllib.error.HTTPError as e:  # 只打印平台返回的错误说明，不含凭证
            print(f"{platform} 登录失败 HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")


if __name__ == "__main__":
    main()
