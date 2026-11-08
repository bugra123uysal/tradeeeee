"""Python motorunu TradingView'daki Pine stratejisinin işlemleriyle karşılaştırır.

TradingView Desktop'un CDP (uzaktan hata ayıklama, port 9222) bağlantısından
grafikteki mumları ve "Buğra Çoklu Hisse Swing Intraday MACD" stratejisinin
işlem listesini okur, aynı mumları sistem/engine.py'den geçirir ve her
girişi / çıkışı mum zamanı + fiyat olarak karşılaştırır.

Kullanım (grafik NORMAL MUM, 1 veya 5 dk, strateji varsayılan ayarlarda olmalı):
    python -m tests.compare_tv [--port 9222] [--macd on|off]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.request import urlopen

import websocket

from sistem.engine import NY, Params, SymbolEngine

STRATEGY = "Buğra Çoklu Hisse Swing Intraday MACD"
PRICE_TOL = 1e-6

EXTRACT_JS = """
(() => {
  const c = window.TradingViewApi.activeChart();
  const m = c._chartWidget.model().model();
  const s = m.dataSources().find(x => x.title && x.title().startsWith(%s));
  if (!s) return JSON.stringify({error: 'strateji grafikte yok'});
  let r = s.reportData(); r = r && r.value ? r.value() : r;
  const bars = []; m.mainSeries().bars().each((i, v) => { bars.push(v.slice(0, 6)); return false; });
  const trades = (r ? r.trades : []).map(t => ({
    ec: t.e.c, ep: t.e.p, et: t.e.tm, xc: t.x && t.x.c, xp: t.x && t.x.p, xt: t.x && t.x.tm}));
  return JSON.stringify({symbol: c.symbol(), resolution: c.resolution(), chartType: c.chartType(),
                         title: s.title(), bars, trades});
})()
""" % json.dumps(STRATEGY)


@dataclass(frozen=True)
class Event:
    kind: str
    ms: int            # mum açılış zamanı (epoch ms)
    price: float


def chart_targets(port: int, target_id: str | None = None) -> list[str]:
    targets = json.load(urlopen(f"http://127.0.0.1:{port}/json"))
    return [t["webSocketDebuggerUrl"] for t in targets
            if t["type"] == "page" and "tradingview.com/chart" in t["url"]
            and (target_id is None or t["id"] == target_id)]


def evaluate(ws_url: str, expr: str) -> dict:
    ws = websocket.create_connection(ws_url, timeout=60, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                return json.loads(msg["result"]["result"]["value"])
    finally:
        ws.close()


def fetch(port: int, target_id: str | None = None) -> dict:
    """Stratejinin olduğu ve en çok mum yüklenmiş grafik sekmesini seçer."""
    best = None
    for url in chart_targets(port, target_id):
        try:
            d = evaluate(url, EXTRACT_JS)
        except Exception:  # noqa: BLE001 — sekme hazır değilse diğerine geç
            continue
        if "error" not in d and (best is None or len(d["bars"]) > len(best["bars"])):
            best = d
    if best is None:
        sys.exit("Stratejinin eklendiği bir TradingView grafiği bulunamadı.")
    return best


def tv_events(trades: list[dict]) -> list[Event]:
    ev = []
    for t in trades:
        ev.append(Event("AL", t["et"], t["ep"]))
        if t.get("xt"):
            ev.append(Event(t["xc"], t["xt"], t["xp"]))
    return sorted(ev, key=lambda e: e.ms)


def py_events(bars: list[list], params: Params) -> list[Event]:
    eng = SymbolEngine("TV", params, prev_close_day=datetime(1970, 1, 1, tzinfo=NY).date())
    ev = []
    for t, o, h, l, c, v in bars:
        ts = datetime.fromtimestamp(t, tz=timezone.utc)
        s = eng.on_bar(ts, o, h, l, c, v or 0.0)
        if s:
            ev.append(Event(s.kind, t * 1000, s.price))
    return ev


def compare(tv: list[Event], py: list[Event], since_ms: int, tick: float) -> tuple[int, list[str]]:
    """Olaylar tür + mum zamanıyla eşleşir; fiyat en fazla 1 tick farklı olabilir
    (TradingView strateji dolum fiyatını tick'e yuvarlar: 5.985 → 5.99)."""
    tv_map = {(e.kind, e.ms): e.price for e in tv if e.ms >= since_ms}
    py_map = {(e.kind, e.ms): e.price for e in py if e.ms >= since_ms}
    fmt = lambda k, p: f"{k[0]:8} {datetime.fromtimestamp(k[1] / 1000, NY):%Y-%m-%d %H:%M} NY  {p:.6g}"
    same, diffs = 0, []
    for k in sorted(tv_map.keys() | py_map.keys(), key=lambda k: k[1]):
        a, b = tv_map.get(k), py_map.get(k)
        if a is not None and b is not None and abs(a - b) <= tick + PRICE_TOL:
            same += 1
        elif a is not None and b is not None:
            diffs.append(f"fiyat farkı  : {fmt(k, a)}  (Python {b:.6g})")
        elif a is not None:
            diffs.append(f"sadece TV    : {fmt(k, a)}")
        else:
            diffs.append(f"sadece Python: {fmt(k, b)}")
    return same, diffs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--macd", choices=["on", "off"], default="on")
    ap.add_argument("--warmup-days", type=int, default=3,
                    help="ilk N gün gösterge ısınması için karşılaştırma dışı")
    ap.add_argument("--target", help="CDP sekme kimliği (birden çok grafik sekmesi açıksa)")
    args = ap.parse_args()

    d = fetch(args.port, args.target)
    tf = int(d["resolution"])
    print(f"{d['symbol']} · {tf} dk · grafik tipi {d['chartType']} · {len(d['bars'])} mum · {len(d['trades'])} TV işlemi")
    print(f"strateji: {d['title']}")
    if d["chartType"] != 1:
        print("⚠ Grafik normal mum değil — TradingView işlemleri sahte fiyatlarla hesaplanır.")

    params = Params(timeframe_min=tf, use_macd=args.macd == "on")
    tv, py = tv_events(d["trades"]), py_events(d["bars"], params)
    first_ms = d["bars"][0][0] * 1000
    since = first_ms + args.warmup_days * 86_400_000
    tick = 0.0001 if min(b[4] for b in d["bars"]) < 1 else 0.01
    ok, diffs = compare(tv, py, since, tick)
    n_tv = sum(1 for e in tv if e.ms >= since)
    print(f"karşılaştırma {datetime.fromtimestamp(since / 1000, NY):%Y-%m-%d} sonrası: "
          f"TV {n_tv} olay · Python {sum(1 for e in py if e.ms >= since)} olay · birebir aynı {ok}")
    for line in diffs[:40]:
        print("  " + line)
    if len(diffs) > 40:
        print(f"  … {len(diffs) - 40} fark daha")


if __name__ == "__main__":
    main()
