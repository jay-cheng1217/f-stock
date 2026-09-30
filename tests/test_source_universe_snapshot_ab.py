"""The A/B IO bridge must preserve exact raw inputs and unknown observations."""
from pathlib import Path
from unittest.mock import patch

import duckdb
import numpy as np
import pandas as pd
import pytest

from scripts import source_universe_snapshot_ab as ab


def test_duckdb_reader_preserves_exact_values_dtypes_and_na(tmp_path):
    frame = pd.DataFrame({'Date':['2026-09-03','2026-09-04'], 'Close':[np.nextafter(20.0,21.0),20.1], 'Volume':pd.Series([120000,150000],dtype='int64'), 'Foreign_BuySell':[np.nan,0.0]})
    metadata = {'7777':{'columns':list(frame), 'dtypes':{c:str(frame[c].dtype) for c in frame}}}
    inserted = frame.assign(_ab_ticker='7777',_ab_row=[0,1])
    conn = duckdb.connect(':memory:')
    conn.register('source',inserted)
    conn.execute('CREATE TABLE daily_k AS SELECT * FROM source')
    try:
        pd.testing.assert_frame_equal(ab.get_raw(conn,metadata,'7777'),frame,check_exact=True)
        with patch.object(ab,'DAILY',tmp_path),ab.duckdb_daily_reader(conn,metadata):
            full = pd.read_csv(tmp_path/'7777.csv',dtype={'Date':str})
            pd.testing.assert_frame_equal(full,frame,check_exact=True)
            sliced = pd.read_csv(tmp_path/'7777.csv',usecols=lambda c:c in {'Date','Close'})
            pd.testing.assert_frame_equal(sliced,frame[['Date','Close']],check_exact=True)
            assert pd.isna(full.Foreign_BuySell.iloc[0])
            assert full.Foreign_BuySell.iloc[1] == 0
            parsed = pd.read_csv(tmp_path/'7777.csv',parse_dates=['Date'])
            assert pd.api.types.is_datetime64_any_dtype(parsed.Date)
            with pytest.raises(ValueError,match='Unexpected canonical CSV options'):
                pd.read_csv(tmp_path/'7777.csv',converters={'Volume':str})
        assert pd.read_csv is ab.READ_CSV
    finally:
        conn.close()


def test_non_daily_reads_stay_with_the_original_reader(tmp_path):
    elsewhere = tmp_path/'aux.csv'
    elsewhere.write_text('Date,Close\n2026-09-04,20\n',encoding='utf-8')
    conn = duckdb.connect(':memory:')
    try:
        with patch.object(ab,'DAILY',tmp_path/'daily'),ab.duckdb_daily_reader(conn,{}):
            assert pd.read_csv(elsewhere).Close.tolist() == [20]
    finally:
        conn.close()


def test_branch_breadth_enumeration_keeps_universe_separate(tmp_path):
    for name in ['2330.csv','7610.csv','notes.txt']:
        (tmp_path/name).write_text('',encoding='utf-8')
    with patch.object(ab,'DAILY',tmp_path):
        with ab.branch_daily_enumeration(['2330']):
            assert [Path(p).name for p in ab.glob.glob(str(tmp_path/'*.csv'))] == ['2330.csv']
        assert {Path(p).name for p in ab.glob.glob(str(tmp_path/'*.csv'))} == {'2330.csv','7610.csv'}
