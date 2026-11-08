"""TradingView Desktop'a CDP (uzaktan hata ayıklama portu) üzerinden bağlanır.

Sayfa içinden yapılan istekler kullanıcının TradingView oturumunu (çerezlerini)
kullanır: tarayıcı (scanner) sorguları ve izleme listesi okuma/yazma bu yolla yapılır.
"""
from __future__ import annotations

import json
from urllib.request import urlopen

import websocket

SCAN_URL = "https://scanner.tradingview.com/america/scan"
LISTS_URL = "/api/v1/symbols_list/custom/"


class TVError(RuntimeError):
    pass


def page_ws_url(port: int = 9222) -> str:
    try:
        targets = json.load(urlopen(f"http://127.0.0.1:{port}/json", timeout=5))
    except OSError as e:
        raise TVError(f"TradingView Desktop'a bağlanılamadı (port {port}). Açık mı? {e}") from e
    pages = [t for t in targets if t.get("type") == "page" and "tradingview.com" in t.get("url", "")]
    if not pages:
        raise TVError("Açık bir tradingview.com sayfası bulunamadı.")
    chart = [t for t in pages if "/chart" in t["url"]]
    return (chart or pages)[0]["webSocketDebuggerUrl"]


def evaluate(js: str, port: int = 9222, timeout: int = 60):
    """js bir Promise döndürebilir; sonuç JSON string olarak dönmeli.
    Bağlantı/zaman aşımı hataları TVError olur (6 Kas: yakalanmayan WebSocketTimeout liste_yenile'yi düşürdü)."""
    try:
        ws = websocket.create_connection(page_ws_url(port), timeout=timeout, suppress_origin=True)
    except (websocket.WebSocketException, OSError) as e:
        raise TVError(f"TradingView bağlantısı kurulamadı: {e}") from e
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": js, "returnByValue": True, "awaitPromise": True}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") != 1:
                continue
            res = msg.get("result", {})
            if "exceptionDetails" in res:
                raise TVError(f"sayfa hatası: {res['exceptionDetails'].get('text')}")
            return json.loads(res["result"]["value"])
    except (websocket.WebSocketException, OSError) as e:
        raise TVError(f"TradingView yanıt vermedi: {e}") from e
    finally:
        ws.close()


def _fetch_js(url: str, method: str = "GET", body=None, text_plain: bool = False) -> str:
    headers = {"Content-Type": "text/plain" if text_plain else "application/json"}
    opts = {"method": method, "credentials": "include", "headers": headers}
    if body is not None:
        opts["body"] = json.dumps(body)
    return f"""(async () => {{
      const r = await fetch({json.dumps(url)}, {json.dumps(opts)});
      const t = await r.text();
      return JSON.stringify({{status: r.status, body: t}});
    }})()"""


def request(url: str, method: str = "GET", body=None, text_plain: bool = False, port: int = 9222):
    r = evaluate(_fetch_js(url, method, body, text_plain), port)
    if r["status"] >= 400:
        raise TVError(f"{method} {url} → HTTP {r['status']}: {r['body'][:200]}")
    return json.loads(r["body"]) if r["body"] else None


def scan(query: dict, port: int = 9222) -> list[dict]:
    """TradingView tarayıcı sorgusu. Dönüş: [{"s": "NASDAQ:ABC", <kolon>: değer, ...}]"""
    body = {k: v for k, v in query.items() if not k.startswith("_")}
    cols = body["columns"]
    res = request(SCAN_URL, "POST", body, text_plain=True, port=port)
    return [{"s": row["s"], **dict(zip(cols, row["d"]))} for row in res.get("data", [])]


def floats(tickers: list[str], port: int = 9222, chunk: int = 400) -> dict[str, float | None]:
    """'BORSA:SEMBOL' → halka açık hisse sayısı (float); yoksa toplam hisse sayısı (üst sınır)."""
    out: dict[str, float | None] = {}
    for i in range(0, len(tickers), chunk):
        q = {"symbols": {"tickers": tickers[i:i + chunk]}, "columns": ["float_shares_outstanding", "total_shares_outstanding"]}
        for row in scan(q, port):
            out[row["s"]] = row["float_shares_outstanding"] or row["total_shares_outstanding"]
    return out


def custom_lists(port: int = 9222) -> list[dict]:
    return request(LISTS_URL, port=port)


def replace_list(list_id: int, symbols: list[str], port: int = 9222) -> None:
    request(f"{LISTS_URL}{list_id}/replace/?unsafe=true", "POST", symbols, port=port)


def create_list(name: str, symbols: list[str], port: int = 9222) -> dict:
    return request(LISTS_URL, "POST", {"name": name, "symbols": symbols}, port=port)
