from scripts.send_daily_health_report import (
    _describe_price_index_return,
    _describe_vix_return,
)


def test_price_index_description_thresholds():
    assert _describe_price_index_return(0.011) == "強勢"
    assert _describe_price_index_return(0.0) == "平盤整理"
    assert _describe_price_index_return(-0.02) == "偏弱"
    assert _describe_price_index_return(-0.0301) == "顯著下跌"


def test_sox_minus_three_percent_is_not_positive_template():
    description = _describe_price_index_return(-0.030112)
    assert description == "顯著下跌"
    assert "穩健" not in description


def test_vix_description_thresholds():
    assert _describe_vix_return(0.06) == "波動顯著升溫"
    assert _describe_vix_return(0.02) == "波動升溫"
    assert _describe_vix_return(0.0) == "波動持平"
    assert _describe_vix_return(-0.0212) == "波動降溫"
