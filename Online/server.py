"""
RPS Extend Online - 中央サーバー（TCP / CUIクライアント用）

対戦ロジックは engine.py（トランスポート非依存）を利用する。
本ファイルはTCPソケットのトランスポートとマッチングのみを担当する。
GUI(ブラウザ)で遊ぶ場合は web_server.py を使う。

使い方:
    python3 server.py [--host 0.0.0.0] [--port 5000]
"""

import argparse
import socket
import threading

import engine
from protocol import Connection


class SocketTransport:
    def __init__(self, conn: Connection):
        self.conn = conn

    def send(self, obj):
        self.conn.send(obj)

    def close(self):
        self.conn.close()


def make_session(conn: Connection, name: str) -> engine.Session:
    session = engine.Session(SocketTransport(conn), name)

    def read_loop():
        while True:
            msg = conn.recv()
            if msg is None:
                session.q.put(engine.DISCONNECT)
                break
            session.q.put(msg)

    threading.Thread(target=read_loop, daemon=True).start()
    return session


def serve(host, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)
    print(f"[RPS Extend Online] TCPサーバー起動: {host}:{port}")
    print("2人の接続を待っています... (Ctrl-C で終了)")

    waiting = []
    lock = threading.Lock()

    def register(conn):
        conn.send({"type": "MSG", "text": "接続しました。名前を送信してください。"})
        msg = conn.recv()
        name = "Player"
        if isinstance(msg, dict) and msg.get("type") == "NAME":
            name = str(msg.get("name") or "Player")[:16]
        session = make_session(conn, name)
        session.msg(f"ようこそ {name} さん。対戦相手を待っています...")
        with lock:
            waiting.append(session)
            if len(waiting) >= 2:
                p0, p1 = waiting.pop(0), waiting.pop(0)
                threading.Thread(target=lambda: engine.Match(p0, p1).run(),
                                 daemon=True).start()

    try:
        while True:
            sock, addr = srv.accept()
            print(f"接続: {addr}")
            conn = Connection(sock)
            threading.Thread(target=register, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        print("\nサーバーを終了します。")
    finally:
        srv.close()


def main():
    ap = argparse.ArgumentParser(description="RPS Extend Online TCPサーバー")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
