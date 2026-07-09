"""
RPS Extend Online - 通信プロトコル

サーバー・クライアント間は「改行区切りのJSON（NDJSON）」でメッセージをやり取りする。
1行 = 1メッセージ（1つのJSONオブジェクト）。

メッセージ種別（type）:

  サーバー → クライアント
    - HELLO   : 接続時の挨拶            {type, text}
    - MSG     : 汎用メッセージ（1行表示） {type, text}
    - STATE   : 自分視点の盤面全体        {type, view}
    - PROMPT  : 入力要求                 {type, pid, text, mode, options}
                 mode = "select" | "text"
                 options = [{key, label}, ...]  (selectのとき)
    - GAMEOVER: 対戦終了                 {type, text}

  クライアント → サーバー
    - NAME   : プレイヤー名           {type, name}
    - CHOICE : PROMPTへの回答         {type, pid, value}
"""

import json
import socket


class Connection:
    """ソケットをNDJSONメッセージ単位で読み書きするラッパ。"""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buf = b""
        self._lock = __import__("threading").Lock()

    def send(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            self.sock.sendall(data)

    def recv(self):
        """次の1メッセージを返す。接続が閉じられたら None。"""
        while b"\n" not in self._buf:
            try:
                chunk = self.sock.recv(4096)
            except OSError:
                return None
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        line = line.strip()
        if not line:
            return self.recv()
        try:
            return json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.recv()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
