"""
RPS Extend Online - クライアント（CUI）

中央サーバーに接続してターミナル上で対戦する。

使い方:
    python3 client.py [--host HOST] [--port 5000] [--name YOURNAME]

サーバーからの表示指示・入力要求に従って番号を入力するだけで対戦できる。
"""

import argparse
import socket
import sys

from protocol import Connection


def ask(text, options):
    """選択肢を表示し、番号または key を受け取って key を返す。"""
    print()
    print(text)
    for idx, opt in enumerate(options, 1):
        print(f"  {idx}) {opt['label']}")
    keys = {opt["key"] for opt in options}
    while True:
        raw = input("> ").strip()
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(options):
                return options[n - 1]["key"]
        if raw in keys:
            return raw
        print("  無効な入力です。番号で選んでください。")


def main():
    ap = argparse.ArgumentParser(description="RPS Extend Online クライアント")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    name = args.name
    if not name:
        try:
            name = input("あなたの名前を入力してください: ").strip() or "Player"
        except EOFError:
            name = "Player"

    sock_host, sock_port = args.host, args.port
    # 接続後すぐに名前を送るため、run内でNAMEを送る
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((sock_host, sock_port))
    except OSError as e:
        print(f"サーバーに接続できません ({sock_host}:{sock_port}): {e}")
        sys.exit(1)
    conn = Connection(sock)
    print(f"サーバー {sock_host}:{sock_port} に接続しました。")

    # サーバーの最初の案内(MSG)を待ってから名前を送る
    first = conn.recv()
    if first and first.get("type") == "MSG":
        print(first.get("text", ""))
    conn.send({"type": "NAME", "name": name})

    _loop(conn)


def _loop(conn):
    while True:
        msg = conn.recv()
        if msg is None:
            print("\nサーバーとの接続が切れました。")
            break
        mtype = msg.get("type")
        if mtype == "MSG":
            print(msg.get("text", ""))
        elif mtype == "HELLO":
            print("\n" + "*" * 52)
            print(msg.get("text", ""))
            print("*" * 52)
        elif mtype == "STATE":
            print()
            for line in msg.get("lines", []):
                print(line)
        elif mtype == "PROMPT":
            if msg.get("mode") == "text":
                print("\n" + msg.get("text", ""))
                value = input("> ").strip()
            else:
                value = ask(msg.get("text", ""), msg.get("options", []))
            conn.send({"type": "CHOICE", "pid": msg.get("pid"), "value": value})
        elif mtype == "GAMEOVER":
            print("\n" + "#" * 52)
            print(msg.get("text", ""))
            print("#" * 52)
            break

    conn.close()


if __name__ == "__main__":
    main()
