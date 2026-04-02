from __future__ import annotations

import glob
import json
import os
from functools import lru_cache
from typing import Any

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from ml.config import DAILY_K_DIR, MODEL_DIR
from ml.features.sector import SECTOR_MAPPING_PATH

MONITOR_REPORT_PATH = os.path.join(MODEL_DIR, "..", "reports", "monitor_t1_latest.json")
from ml.cross_confirm import get_dual_confirmed_tickers, load_cross_confirmed


def _load_name_lookup() -> dict[str, str]:
    """Ticker -> 股票名稱 dict"""
    if not os.path.exists(SECTOR_MAPPING_PATH):
        return {}
    df = pd.read_csv(SECTOR_MAPPING_PATH, dtype={"Ticker": str})
    if "Name" not in df.columns:
        return {}
    return dict(zip(df["Ticker"], df["Name"]))


_NAME_LOOKUP: dict[str, str] = _load_name_lookup()
from ml.predict_t1 import (
    load_latest_t1_model,
    load_latest_t1_predictions_csv,
    predict_t1_all,
    sort_t1_prediction_df,
)

router = APIRouter(tags=["t1"])

DEFAULT_T1_RULES = {
    "top_n": 10,
    "min_prob": 0.60,
    "take_profit": 0.05,
    "stop_loss": None,
    "friction": 0.004,
    "ambiguous_fill": "close",
}


def _safe_int(value: Any, default: int = 0) -> int:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return default
    return int(numeric)


def _selected_mask(series: pd.Series) -> pd.Series:
    text = series.fillna(False).astype(str).str.strip().str.lower()
    return text.isin({"1", "true", "yes"})


def _extract_trade_rules(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not meta:
        return DEFAULT_T1_RULES.copy()

    trade_rules = meta.get("trade_rules", {}) or {}
    selection = trade_rules.get("selection", {}) or {}

    stop_loss = trade_rules.get("stop_loss")
    if stop_loss in ("", 0):
        stop_loss = None

    take_profit = trade_rules.get("take_profit")
    if take_profit in ("", 0):
        take_profit = None

    return {
        "top_n": int(selection.get("top_n", DEFAULT_T1_RULES["top_n"]) or DEFAULT_T1_RULES["top_n"]),
        "min_prob": float(selection.get("min_prob", DEFAULT_T1_RULES["min_prob"]) or DEFAULT_T1_RULES["min_prob"]),
        "take_profit": float(take_profit) if take_profit is not None else None,
        "stop_loss": float(stop_loss) if stop_loss is not None else None,
        "friction": float(trade_rules.get("friction", DEFAULT_T1_RULES["friction"]) or DEFAULT_T1_RULES["friction"]),
        "ambiguous_fill": str(trade_rules.get("ambiguous_fill", DEFAULT_T1_RULES["ambiguous_fill"])),
    }


def _get_live_t1_prediction_df() -> tuple[pd.DataFrame, dict[str, Any] | None]:
    csv_df = load_latest_t1_predictions_csv()
    meta: dict[str, Any] | None = None
    try:
        _, meta = load_latest_t1_model()
    except FileNotFoundError:
        meta = None

    if csv_df is not None and not csv_df.empty:
        return csv_df, meta

    if meta is None:
        raise FileNotFoundError(
            "尚未建立 T+1 模型與預測檔。請先執行 scripts/train_t1_backtest.py，再跑一次 python -m ml.predict_t1。"
        )

    return predict_t1_all(save_csv=True, verbose=False)


def _prepare_t1_prediction_export_df(df: pd.DataFrame) -> pd.DataFrame:
    export_df = df.copy()

    if "hit_prob_3pct" in export_df.columns:
        export_df["hit_prob_3pct"] = (pd.to_numeric(export_df["hit_prob_3pct"], errors="coerce") * 100).round(2)
    if "t1_score" in export_df.columns:
        export_df["t1_score"] = pd.to_numeric(export_df["t1_score"], errors="coerce").round(4)
    for col in ["take_profit", "stop_loss", "friction", "t1_breakout_20d", "t1_inst_net_ratio_1d", "t1_upper_wick_pct"]:
        if col in export_df.columns:
            export_df[col] = (pd.to_numeric(export_df[col], errors="coerce") * 100).round(2)
    if "t1_volume_burst_5d" in export_df.columns:
        export_df["t1_volume_burst_5d"] = pd.to_numeric(export_df["t1_volume_burst_5d"], errors="coerce").round(2)

    rename_map = {
        "date": "預測日期",
        "ticker": "股票代號",
        "close": "當日收盤",
        "hit_prob_3pct": "明日觸及3%機率(%)",
        "t1_score": "T+1排序分數",
        "recommendation": "推薦等級",
        "setup_tags": "型態亮點",
        "risk_tags": "風險標籤",
        "sector": "產業",
        "selected_for_trade": "納入策略交易",
        "selection_rank": "交易順位",
        "take_profit": "停利門檻(%)",
        "stop_loss": "停損門檻(%)",
        "friction": "摩擦成本(%)",
        "t1_volume_burst_5d": "爆量比(5日)",
        "t1_inst_net_ratio_1d": "法人單日佔量比(%)",
        "t1_breakout_20d": "相對20日高點距離(%)",
        "t1_close_location": "收盤位置",
        "t1_upper_wick_pct": "上影線比例(%)",
    }
    ordered = [col for col in rename_map if col in export_df.columns]
    ordered.extend(col for col in export_df.columns if col not in ordered)
    return export_df.loc[:, ordered].rename(columns={k: v for k, v in rename_map.items() if k in export_df.columns})


@lru_cache(maxsize=4096)
def _load_daily_price_frame(ticker: str) -> pd.DataFrame:
    path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])

    df = pd.read_csv(path, usecols=["Date", "Open", "High", "Low", "Close"], dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def _simulate_t1_trade(
    entry_close: float,
    next_bar: pd.Series,
    take_profit: float | None,
    friction: float,
    stop_loss: float | None,
    ambiguous_fill: str,
) -> dict[str, Any]:
    open_ret = float(next_bar["Open"] / entry_close - 1.0)
    close_ret = float(next_bar["Close"] / entry_close - 1.0)
    high_ret = float(next_bar["High"] / entry_close - 1.0)
    low_ret = float(next_bar["Low"] / entry_close - 1.0)

    hit_take_profit = take_profit is not None and high_ret >= take_profit
    hit_stop_loss = stop_loss is not None and low_ret <= -stop_loss

    if stop_loss is not None and open_ret <= -stop_loss:
        gross_return = open_ret
        exit_reason = "gap_stop"
    elif take_profit is not None and open_ret >= take_profit:
        gross_return = take_profit
        exit_reason = "gap_take_profit"
    elif hit_take_profit and hit_stop_loss:
        if ambiguous_fill == "target_first":
            gross_return = take_profit
            exit_reason = "ambiguous_take_profit"
        elif ambiguous_fill == "close":
            gross_return = close_ret
            exit_reason = "ambiguous_close"
        else:
            gross_return = -stop_loss
            exit_reason = "ambiguous_stop_loss"
    elif hit_take_profit:
        gross_return = take_profit
        exit_reason = "take_profit"
    elif hit_stop_loss and stop_loss is not None:
        gross_return = -stop_loss
        exit_reason = "stop_loss"
    else:
        gross_return = close_ret
        exit_reason = "close"

    net_return = gross_return - friction
    exit_price = entry_close * (1.0 + gross_return)
    return {
        "open_return": open_ret,
        "high_return": high_ret,
        "low_return": low_ret,
        "close_return": close_ret,
        "gross_return": gross_return,
        "net_return": net_return,
        "exit_reason": exit_reason,
        "exit_price": exit_price,
    }


def _load_t1_portfolio_detail_and_summary() -> tuple[pd.DataFrame, dict[str, Any]]:
    pred_files = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv")))
    columns = [
        "prediction_date",
        "ticker",
        "selection_rank",
        "recommendation",
        "entry_close",
        "hit_prob_3pct",
        "exit_date",
        "exit_price",
        "net_return_pct",
        "gross_return_pct",
        "high_return_pct",
        "low_return_pct",
        "close_return_pct",
        "exit_reason",
        "status",
    ]

    if not pred_files:
        return pd.DataFrame(columns=columns), {
            "total_positions": 0,
            "closed_positions": 0,
            "pending_positions": 0,
            "avg_net_return_pct": None,
            "win_rate_pct": None,
            "take_profit_hits": 0,
            "stop_loss_hits": 0,
            "latest_exit_date": None,
            "trade_rules": DEFAULT_T1_RULES.copy(),
        }

    try:
        _, meta = load_latest_t1_model()
    except FileNotFoundError:
        meta = None
    trade_rules = _extract_trade_rules(meta)

    records: list[dict[str, Any]] = []
    for pred_file in pred_files:
        pred_df = pd.read_csv(pred_file, dtype={"ticker": str})
        if pred_df.empty:
            continue

        pred_df = sort_t1_prediction_df(pred_df)
        if "selected_for_trade" in pred_df.columns:
            selected_mask = _selected_mask(pred_df["selected_for_trade"])
            picks = pred_df[selected_mask].copy()
        else:
            picks = pred_df[pred_df["hit_prob_3pct"] >= trade_rules["min_prob"]].head(trade_rules["top_n"]).copy()

        if picks.empty:
            continue

        prediction_date = pd.Timestamp(str(picks["date"].iloc[0])).normalize()
        if "selection_rank" not in picks.columns:
            picks["selection_rank"] = range(1, len(picks) + 1)

        for _, row in picks.iterrows():
            ticker = str(row["ticker"])
            entry_close = float(pd.to_numeric(row.get("close"), errors="coerce"))
            price_df = _load_daily_price_frame(ticker)
            next_rows = price_df[price_df["Date"] > prediction_date]
            if entry_close <= 0 or next_rows.empty:
                records.append(
                    {
                        "prediction_date": prediction_date.strftime("%Y-%m-%d"),
                        "ticker": ticker,
                        "selection_rank": _safe_int(row.get("selection_rank")),
                        "recommendation": str(row.get("recommendation", "")),
                        "entry_close": round(entry_close, 2) if entry_close > 0 else None,
                        "hit_prob_3pct": round(float(pd.to_numeric(row.get("hit_prob_3pct"), errors="coerce")) * 100, 2)
                        if pd.notna(pd.to_numeric(row.get("hit_prob_3pct"), errors="coerce"))
                        else None,
                        "exit_date": None,
                        "exit_price": None,
                        "net_return_pct": None,
                        "gross_return_pct": None,
                        "high_return_pct": None,
                        "low_return_pct": None,
                        "close_return_pct": None,
                        "exit_reason": "待隔日結算",
                        "status": "pending",
                    }
                )
                continue

            next_bar = next_rows.iloc[0]
            trade = _simulate_t1_trade(
                entry_close=entry_close,
                next_bar=next_bar,
                take_profit=trade_rules["take_profit"],
                friction=trade_rules["friction"],
                stop_loss=trade_rules["stop_loss"],
                ambiguous_fill=trade_rules["ambiguous_fill"],
            )
            records.append(
                    {
                        "prediction_date": prediction_date.strftime("%Y-%m-%d"),
                        "ticker": ticker,
                        "selection_rank": _safe_int(row.get("selection_rank")),
                        "recommendation": str(row.get("recommendation", "")),
                    "entry_close": round(entry_close, 2),
                    "hit_prob_3pct": round(float(pd.to_numeric(row.get("hit_prob_3pct"), errors="coerce")) * 100, 2)
                    if pd.notna(pd.to_numeric(row.get("hit_prob_3pct"), errors="coerce"))
                    else None,
                    "exit_date": pd.Timestamp(next_bar["Date"]).strftime("%Y-%m-%d"),
                    "exit_price": round(float(trade["exit_price"]), 2),
                    "net_return_pct": round(float(trade["net_return"]) * 100, 2),
                    "gross_return_pct": round(float(trade["gross_return"]) * 100, 2),
                    "high_return_pct": round(float(trade["high_return"]) * 100, 2),
                    "low_return_pct": round(float(trade["low_return"]) * 100, 2),
                    "close_return_pct": round(float(trade["close_return"]) * 100, 2),
                    "exit_reason": str(trade["exit_reason"]),
                    "status": "closed",
                }
            )

    detail_df = pd.DataFrame(records, columns=columns)
    if detail_df.empty:
        return detail_df, {
            "total_positions": 0,
            "closed_positions": 0,
            "pending_positions": 0,
            "avg_net_return_pct": None,
            "win_rate_pct": None,
            "take_profit_hits": 0,
            "stop_loss_hits": 0,
            "latest_exit_date": None,
            "trade_rules": trade_rules,
        }

    detail_df = detail_df.sort_values(
        ["prediction_date", "selection_rank", "ticker"],
        ascending=[False, True, True],
    ).reset_index(drop=True)

    closed_df = detail_df[detail_df["status"] == "closed"].copy()
    take_profit_hits = 0
    stop_loss_hits = 0
    if not closed_df.empty:
        exit_reason = closed_df["exit_reason"].fillna("")
        take_profit_hits = int(exit_reason.str.contains("take_profit").sum())
        stop_loss_hits = int(exit_reason.str.contains("stop").sum())

    summary = {
        "total_positions": int(len(detail_df)),
        "closed_positions": int(len(closed_df)),
        "pending_positions": int((detail_df["status"] == "pending").sum()),
        "avg_net_return_pct": round(float(v), 2) if not closed_df.empty and pd.notna(v := closed_df["net_return_pct"].mean()) else None,
        "win_rate_pct": round(float(v2), 2) if not closed_df.empty and pd.notna(v2 := (closed_df["net_return_pct"].dropna() > 0).mean() * 100) else None,
        "take_profit_hits": take_profit_hits,
        "stop_loss_hits": stop_loss_hits,
        "latest_exit_date": closed_df["exit_date"].max() if not closed_df.empty else None,
        "trade_rules": trade_rules,
    }
    return detail_df, summary


def _prepare_t1_portfolio_export_df(detail_df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "prediction_date": "進場日",
        "ticker": "股票代號",
        "selection_rank": "交易順位",
        "recommendation": "推薦等級",
        "entry_close": "進場收盤",
        "hit_prob_3pct": "明日觸及3%機率(%)",
        "exit_date": "出場日",
        "exit_price": "出場價",
        "net_return_pct": "淨報酬(%)",
        "gross_return_pct": "毛報酬(%)",
        "high_return_pct": "隔日高點報酬(%)",
        "low_return_pct": "隔日低點報酬(%)",
        "close_return_pct": "隔日收盤報酬(%)",
        "exit_reason": "出場方式",
        "status": "狀態",
    }
    ordered = [col for col in rename_map if col in detail_df.columns]
    ordered.extend(col for col in detail_df.columns if col not in ordered)
    return detail_df.loc[:, ordered].rename(columns={k: v for k, v in rename_map.items() if k in detail_df.columns})


@router.get("/api/predictions/t1/latest")
def latest_t1_predictions(top_n: int = 30):
    try:
        df, meta = _get_live_t1_prediction_df()
    except FileNotFoundError as exc:
        return JSONResponse(
            content={"status": "missing_model", "error": str(exc), "data": []},
            status_code=404,
        )
    except Exception as exc:
        return JSONResponse(
            content={"status": "error", "error": str(exc), "data": []},
            status_code=500,
        )

    top_df = df.head(top_n).copy()
    trade_rules = _extract_trade_rules(meta)
    rec_dist = (
        {str(k): int(v) for k, v in df["recommendation"].value_counts().items()}
        if "recommendation" in df.columns
        else {}
    )

    records = []
    for _, row in top_df.iterrows():
        records.append(
            {
                "ticker": str(row.get("ticker", "")),
                "name": _NAME_LOOKUP.get(str(row.get("ticker", "")), ""),
                "close": round(float(pd.to_numeric(row.get("close"), errors="coerce")), 2)
                if pd.notna(pd.to_numeric(row.get("close"), errors="coerce"))
                else None,
                "hit_prob_3pct": round(float(pd.to_numeric(row.get("hit_prob_3pct"), errors="coerce")), 4)
                if pd.notna(pd.to_numeric(row.get("hit_prob_3pct"), errors="coerce"))
                else None,
                "t1_score": round(float(pd.to_numeric(row.get("t1_score"), errors="coerce")), 4)
                if pd.notna(pd.to_numeric(row.get("t1_score"), errors="coerce"))
                else None,
                "recommendation": str(row.get("recommendation", "")),
                "setup_tags": str(row.get("setup_tags", "")),
                "risk_tags": str(row.get("risk_tags", "")) if pd.notna(row.get("risk_tags")) and str(row.get("risk_tags", "")).strip() not in ("", "nan") else "",
                "sector": str(row.get("sector", "")),
                "selected_for_trade": bool(row.get("selected_for_trade", False)),
                "selection_rank": int(row["selection_rank"])
                if pd.notna(row.get("selection_rank"))
                else None,
                "volume_burst_5d": round(float(pd.to_numeric(row.get("t1_volume_burst_5d"), errors="coerce")), 2)
                if pd.notna(pd.to_numeric(row.get("t1_volume_burst_5d"), errors="coerce"))
                else None,
                "inst_net_ratio_1d": round(float(pd.to_numeric(row.get("t1_inst_net_ratio_1d"), errors="coerce")) * 100, 2)
                if pd.notna(pd.to_numeric(row.get("t1_inst_net_ratio_1d"), errors="coerce"))
                else None,
                "breakout_20d": round(float(pd.to_numeric(row.get("t1_breakout_20d"), errors="coerce")) * 100, 2)
                if pd.notna(pd.to_numeric(row.get("t1_breakout_20d"), errors="coerce"))
                else None,
                "support_1": round(float(row["support_1"]), 2)
                if pd.notna(row.get("support_1"))
                else None,
                "support_1_src": str(row.get("support_1_src", ""))
                if pd.notna(row.get("support_1_src"))
                else None,
                "resistance_1": round(float(row["resistance_1"]), 2)
                if pd.notna(row.get("resistance_1"))
                else None,
                "suggested_entry": round(float(row["suggested_entry"]), 2)
                if pd.notna(row.get("suggested_entry"))
                else None,
                "entry_discount_pct": round(float(row["entry_discount_pct"]) * 100, 2)
                if pd.notna(row.get("entry_discount_pct"))
                else None,
                "level_source": str(row.get("level_source", ""))
                if pd.notna(row.get("level_source"))
                else None,
                "support_strength": str(row.get("support_strength", ""))
                if pd.notna(row.get("support_strength"))
                else None,
                "entry_note": str(row.get("entry_note", ""))
                if pd.notna(row.get("entry_note"))
                else None,
                "trade_route": str(row.get("trade_route", ""))
                if pd.notna(row.get("trade_route")) and str(row.get("trade_route", "")).strip() not in ("", "nan")
                else None,
                "ma5_bias": round(float(row["ma5_bias"]) * 100, 2)
                if pd.notna(row.get("ma5_bias"))
                else None,
            }
        )

    return {
        "status": "success",
        "prediction_date": str(df["date"].iloc[0]) if not df.empty else None,
        "total_stocks": int(len(df)),
        "selected_count": int(df["selected_for_trade"].fillna(False).sum()) if "selected_for_trade" in df.columns else 0,
        "avg_hit_prob_top": round(float(top_df["hit_prob_3pct"].mean()) * 100, 2) if not top_df.empty else None,
        "trade_rules": trade_rules,
        "rec_dist": rec_dist,
        "data": records,
    }


@router.get("/api/predictions/t1/download")
def download_t1_predictions(top_n: int = 30):
    try:
        df, _ = _get_live_t1_prediction_df()
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    export_df = _prepare_t1_prediction_export_df(df.head(top_n).copy())
    pred_date = str(df["date"].iloc[0]) if not df.empty else "unknown"
    csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="predictions_t1_top{top_n}_{pred_date}.csv"'},
    )


@router.get("/api/portfolio/t1")
def get_t1_portfolio():
    try:
        detail_df, summary = _load_t1_portfolio_detail_and_summary()
        detail_records = [] if detail_df.empty else detail_df.astype(object).where(detail_df.notna(), None).to_dict(orient="records")
        return JSONResponse(
            content={"status": "success", "data": detail_records, "summary": summary}
        )
    except Exception as exc:
        return JSONResponse(
            content={"status": "error", "message": str(exc)},
            status_code=500,
        )


@router.get("/api/portfolio/t1/download")
def download_t1_portfolio():
    try:
        detail_df, summary = _load_t1_portfolio_detail_and_summary()
        export_df = _prepare_t1_portfolio_export_df(detail_df)
        file_date = summary.get("latest_exit_date") or "latest"
        csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
        return StreamingResponse(
            iter([csv_bytes]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="paper_portfolio_t1_{file_date}.csv"'},
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/predictions/cross-confirm")
def get_cross_confirmed(top_n: int = 10):
    """Return stocks confirmed by both T+1 and 20D models."""
    try:
        merged = load_cross_confirmed()
        if merged.empty:
            return {"status": "success", "data": [], "total": 0, "confirmed_count": 0}

        confirmed = merged[merged["cross_both_buy"]].head(top_n)
        conflict = merged[merged["cross_conflict"]]

        records = []
        for _, row in confirmed.iterrows():
            records.append({
                "ticker": str(row["ticker"]),
                "t1_prob": round(float(row.get("t1_prob", 0)), 4) if pd.notna(row.get("t1_prob")) else None,
                "t1_rank": int(row["t1_rank"]) if pd.notna(row.get("t1_rank")) else None,
                "d20_pred_return": round(float(row.get("d20_pred_return", 0)), 4) if pd.notna(row.get("d20_pred_return")) else None,
                "d20_recommendation": str(row.get("d20_recommendation", "")),
                "cross_score": round(float(row.get("cross_score", 0)), 4),
                "cross_label": str(row.get("cross_label", "")),
            })

        return {
            "status": "success",
            "total": len(merged),
            "confirmed_count": len(merged[merged["cross_both_buy"]]),
            "conflict_count": len(conflict),
            "data": records,
        }
    except Exception as exc:
        return JSONResponse(
            content={"status": "error", "error": str(exc), "data": []},
            status_code=500,
        )


@router.get("/api/monitor/t1")
def get_t1_monitor():
    """Return latest T+1 model monitoring report."""
    rpath = os.path.normpath(MONITOR_REPORT_PATH)
    if not os.path.exists(rpath):
        return JSONResponse(
            content={"status": "error", "message": "No monitor report yet"},
            status_code=404,
        )
    try:
        with open(rpath, "r", encoding="utf-8") as f:
            report = json.load(f)
        return JSONResponse(content={"status": "success", **report})
    except Exception as exc:
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)
