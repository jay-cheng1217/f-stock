"""Pydantic 模型定義."""

from pydantic import BaseModel


class StockInfo(BaseModel):
    ticker: str
    name: str
    last_close: float | None = None
    last_volume: int | None = None
    last_date: str | None = None
    last_change_pct: float | None = None


class DailyKRow(BaseModel):
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    foreign_buysell: float | None = None
    trust_buysell: float | None = None
    dealer_buysell: float | None = None
    margin_balance: float | None = None
    short_balance: float | None = None
    ma_5: float | None = None
    ma_20: float | None = None
    ma_60: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    k: float | None = None
    d: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    vol_ma_5: float | None = None
    vol_ma_20: float | None = None


class RevenueRow(BaseModel):
    date: str
    name: str | None = None
    monthly_revenue: float | None = None
    cumulative_revenue: float | None = None
    yoy_pct_change: float | None = None
    cumulative_yoy_pct_change: float | None = None


class FinancialRow(BaseModel):
    ticker: str
    name: str | None = None
    revenue_m: float | None = None
    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    pretax_margin_pct: float | None = None
    net_margin_pct: float | None = None
    market: str | None = None
    year: int | None = None
    season: int | None = None


class DimensionScore(BaseModel):
    score: float
    details: str


class StockScore(BaseModel):
    ticker: str
    name: str
    total_score: float
    signal: str  # 強力買進/買進/持有/賣出
    technical: DimensionScore
    fundamental: DimensionScore
    chip: DimensionScore  # 籌碼面
    sentiment: DimensionScore  # 消息面
    summary: str  # 中文綜合說明


class RankingItem(BaseModel):
    rank: int
    ticker: str
    name: str
    value: float
    extra: dict | None = None


class IndexInfo(BaseModel):
    name: str
    last_close: float
    change: float
    change_pct: float
    last_date: str


class MarketOverview(BaseModel):
    indices: list[IndexInfo]
    top_gainers: list[StockInfo]
    top_losers: list[StockInfo]
    top_volume: list[StockInfo]
