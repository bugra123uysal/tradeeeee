"""backtest/sonuc/*.csv özetleri (python -m backtest.rapor)."""
from __future__ import annotations

import pandas as pd

from .calistir import OUT, VARIANTS

LIST_PM = set("BURU SLGB TNMG PTHS OSPN ASUR RMNI NCPL CVEO VIVK WKHS CDNA ACCO STRZ INO ONEG WFF FMC GDYN "
              "CPRI AIOK CVCO SEGG BTSG GITS AZIO AMSS RKT NRXP REGN RIVN PLAG NMRA ACH SMP LGCL XCUR MGRX "
              "AURE FRGT FDXF".split())
LIST_TR = set("ONEG CHOW SGRX SLGB GDYN FMC ASUR NFE TNMG OSPN ZJYL ACCO RMNI CDNA XPOF PAVS CVEO CPRI WKHS "
              "FDXF FRGT REGN CBAT GPN ECX PAAI BTSG ANVS GDDY TPET OLN DFIN NVA HGV RKT MHK MIAX GYGY VLTO "
              "SPOT KOS TMUS RIVN AN COLM BBIO TYL REAL TGTX STGW NSP BAH ATR".split())
LIST_START = pd.Timestamp("2026-10-19").date()


def summary(t: pd.DataFrame) -> pd.Series:
    if t.empty:
        return pd.Series({"işlem": 0})
    return pd.Series({
        "işlem": len(t), "kârlı %": round((t["net"] > 0).mean() * 100),
        "ort. net %": round(t["net"].mean() * 100, 2), "toplam net %": round(t["net"].sum() * 100, 1),
        "ort. R": round(t["r"].mean(), 2), "hedef %": round((t["reason"] == "HEDEF").mean() * 100),
    })


def bucket(cd: pd.DataFrame, col: str, bins, labels, outcome: str) -> pd.DataFrame:
    x = cd.dropna(subset=[col, outcome]).copy()
    x["grup"] = pd.cut(x[col], bins=bins, labels=labels, right=False)
    g = x.groupby("grup", observed=True)[outcome]
    return pd.DataFrame({"adet": g.size(), "ort %": (g.mean() * 100).round(1),
                         "medyan %": (g.median() * 100).round(1),
                         "kazanan %": (g.apply(lambda v: (v > 0).mean()) * 100).round(0)})


def main() -> None:
    tr = pd.read_csv(OUT / "islemler.csv", sep=";", encoding="utf-8-sig", parse_dates=["day"])
    cd = pd.read_csv(OUT / "adaylar.csv", sep=";", encoding="utf-8-sig", parse_dates=["day"])
    tr["day"], cd["day"] = tr["day"].dt.date, cd["day"].dt.date
    pd.set_option("display.width", 200)

    print("=== 1. Cameron kuralları — TÜM PİYASA (", cd["day"].min(), "–", cd["day"].max(), ") ===")
    print(tr.groupby("variant").apply(summary, include_groups=False).to_string())

    in_list = tr[(tr["day"] >= LIST_START) & tr["symbol"].isin(LIST_PM | LIST_TR)]
    print("\n=== 2. Cameron — SENİN LİSTELERİN (19–30 Ekim) ===")
    print(in_list.groupby("variant").apply(summary, include_groups=False).to_string())
    v0 = list(VARIANTS)[0]
    print(f"\n--- {v0}: listedeki işlemler ---")
    cols = ["symbol", "day", "entry_time", "entry", "exit_time", "exit", "reason", "net"]
    print(in_list[in_list["variant"] == v0][cols].assign(net=lambda d: (d["net"] * 100).round(1)).to_string(index=False))

    print("\n=== 3. Cameron saat dağılımı (tam kural, tüm piyasa) ===")
    t0 = tr[tr["variant"] == v0].copy()
    t0["saat"] = t0["entry_time"].str[:2]
    print(t0.groupby("saat").apply(summary, include_groups=False).to_string())

    pm = cd[cd["pm_ok"] == True].copy()  # noqa: E712
    pm["pm_dollar"] = pm["pm_vol"] * pm["pm_price"]
    pm["pm_rel"] = pm["pm_vol"] / pm["adv20"]
    print(f"\n=== 4. PREMARKET tarayıcısını geçenler (09:25): {len(pm)} hisse-gün ===")
    print("Sonuç ölçüsü: açılışta al, 11:00'de sat (open_to_1100) ve açılış→kapanış")
    specs = [
        ("pm_gain", [0.05, 0.10, 0.20, 0.40, 1, 99], ["5-10%", "10-20%", "20-40%", "40-100%", "100%+"]),
        ("pm_price", [0, 1, 2, 5, 10, 20, 1e6], ["<1$", "1-2$", "2-5$", "5-10$", "10-20$", "20$+"]),
        ("float", [0, 5e6, 10e6, 20e6, 50e6, 1e13], ["<5M", "5-10M", "10-20M", "20-50M", "50M+"]),
        ("pm_rel", [0, 0.05, 0.2, 0.5, 1, 1e9], ["<%5", "%5-20", "%20-50", "%50-100", ">%100 ADV"]),
        ("pm_dollar", [0, 1e5, 1e6, 5e6, 1e12], ["<100K$", "100K-1M$", "1-5M$", "5M$+"]),
        ("pm_high_fade", [-1, -0.15, -0.05, 0.0001], ["tepeden >%15 aşağı", "%5-15 aşağı", "tepeye yakın (<%5)"]),
    ]
    for col, bins, labels in specs:
        print(f"\n-- {col} --  (açılış→11:00 tepe | açılış→kapanış)")
        a = bucket(pm, col, bins, labels, "open_to_high90")
        b = bucket(pm, col, bins, labels, "open_to_close")
        print(a.join(b, rsuffix=" kapanış").to_string())

    trc = cd[cd["tr_ok"] == True].copy()  # noqa: E712
    print(f"\n=== 5. PİYASA tarayıcısı (ilk girdiği an → kapanış): {len(trc)} hisse-gün ===")
    trc["saat"] = trc["tr_time"].str[:2]
    trc["pm_once"] = trc["pm_ok"].map({True: "premarket'te de vardı", False: "sadece piyasada"})
    for col in ["saat", "pm_once"]:
        g = trc.groupby(col)["tr_to_close"]
        print(pd.DataFrame({"adet": g.size(), "ort %": (g.mean() * 100).round(1),
                            "medyan %": (g.median() * 100).round(1),
                            "kazanan %": (g.apply(lambda v: (v > 0).mean()) * 100).round(0)}).to_string())
    trc["fl"] = pd.cut(trc["float"], [0, 20e6, 1e13], labels=["float<20M", "float>20M"])
    g = trc.groupby("fl", observed=True)["tr_to_close"]
    print(pd.DataFrame({"adet": g.size(), "ort %": (g.mean() * 100).round(1),
                        "medyan %": (g.median() * 100).round(1)}).to_string())


if __name__ == "__main__":
    main()
