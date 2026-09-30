import pandas as pd
import pytest

from scripts.stage_margin_identifier_history import patch_cells


def test_multi_date_patch_preserves_prices_true_zero_and_untouched_bytes():
    raw = b'\xef\xbb\xbfDate,Close,Margin_Balance,Short_Balance\r\n2026-09-01,75.59999847,,\r\n2026-09-02,77.2,0,4\r\n2026-09-03,78.0,6,7\r\n'
    delta = pd.DataFrame([['2026-09-01', 'Margin_Balance', None, 0], ['2026-09-02', 'Short_Balance', 4, 9]], columns=['Date', 'column', 'old', 'new'])
    result, before, after = patch_cells(raw, delta)
    assert result.startswith(b'\xef\xbb\xbf') and result.splitlines(keepends=True)[-1] == raw.splitlines(keepends=True)[-1]
    assert before.Close.equals(after.Close) and after.Margin_Balance.iloc[0] == 0
    assert after.Short_Balance.iloc[1] == 9 and pd.isna(after.Short_Balance.iloc[0])


@pytest.mark.parametrize('field,day,old,new', [('Close', '2026-09-01', 75.6, 0), ('Margin_Balance', '2026-09-02', 1, 2), ('Margin_Balance', '2026-09-01', 9, 2), ('Margin_Balance', '2026-09-01', 1, None)])
def test_rejects_non_margin_absent_bar_stale_value_or_unknown_replacement(field, day, old, new):
    raw = b'Date,Close,Margin_Balance,Short_Balance\n2026-09-01,75.6,1,2\n'
    delta = pd.DataFrame([[day, field, old, new]], columns=['Date', 'column', 'old', 'new'])
    with pytest.raises((ValueError, TypeError)):
        patch_cells(raw, delta)
