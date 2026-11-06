"""在终端里逐项粘贴凭证（输入不显示），写入 ~/.config/tendollaragent/credentials.env。

直接回车 = 保留原来的值。写完文件权限是 600，只有你能读写。
用法：python3 ~/tendollaragent/scripts/set_credentials.py
"""
import getpass
from pathlib import Path

CREDENTIALS_FILE = Path.home() / ".config/tendollaragent/credentials.env"
FIELDS = [
    ("BSKY_HANDLE", "Bluesky handle", False),
    ("BSKY_APP_PASSWORD", "Bluesky app password", True),
    ("X_API_KEY", "X API Key", True),
    ("X_API_SECRET", "X API Key Secret", True),
    ("X_ACCESS_TOKEN", "X Access Token", True),
    ("X_ACCESS_TOKEN_SECRET", "X Access Token Secret", True),
]


def read_existing():
    values = {}
    if CREDENTIALS_FILE.exists():
        for line in CREDENTIALS_FILE.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def main():
    values = read_existing()
    print("逐项粘贴后回车；直接回车保留原值。密钥类输入不会显示在屏幕上。\n")
    for key, label, is_secret in FIELDS:
        status = "已有旧值：回车保留，粘贴新值替换" if values.get(key) else "当前为空，请粘贴"
        prompt = f"{label}（{status}）: "
        entered = getpass.getpass(prompt) if is_secret else input(prompt)
        if entered.strip():
            values[key] = entered.strip()

    lines = ["# TenDollarAgent 凭证文件，只在本机，权限 600。脚本运行时读取，不会打印。"]
    lines += [f"{key}={values.get(key, '')}" for key, _, _ in FIELDS]
    CREDENTIALS_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text("\n".join(lines) + "\n")
    CREDENTIALS_FILE.chmod(0o600)

    print("\n已保存。各项状态：")
    for key, label, _ in FIELDS:
        print(f"  {label:24s} {'已填' if values.get(key) else '空'}")


if __name__ == "__main__":
    main()
