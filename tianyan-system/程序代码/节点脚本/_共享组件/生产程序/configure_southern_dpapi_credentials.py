from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from runtime_workspace import load_workspace
from southern_dpapi_credentials import write_credentials


PROJECT_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "AGENTS.md").is_file() and (parent / "本机配置" / "runtime.local.json").is_file()
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Store Southern Fund login credentials with Windows user-bound DPAPI.")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "本机配置" / "southern_login.dpapi",
    )
    args = parser.parse_args()
    load_workspace(PROJECT_ROOT)
    login_id = input("南方基金登录账号/手机号（输入不写日志）：").strip()
    password = getpass.getpass("南方基金登录密码（不回显）：")
    confirmation = getpass.getpass("再次输入密码（不回显）：")
    if password != confirmation:
        raise SystemExit("两次密码不一致，未写入。")
    write_credentials(args.output.resolve(), login_id, password)
    print(f"已写入 Windows 当前用户绑定的 DPAPI 凭据：{args.output.resolve()}")


if __name__ == "__main__":
    main()
