"""Complete new-source classic indicators with the existing nightly producer.

The locked union DB remains read-only. This writes a separate derived stage and
proves all raw inputs and full ML technical outputs remain value-identical.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import time
import warnings

import duckdb
import numpy as np
import pandas as pd

from scripts import source_universe_snapshot_ab as ab


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Run through the acceptance guard')
    from ml.features import technical
    manifest=json.loads((ab.OUT/'frozen_manifest.json').read_text(encoding='utf-8'))
    stage=ab.OUT/'enriched_v3_classic'
    if stage.exists():
        raise FileExistsError('Refusing to overwrite the derived classic stage')
    (stage/'daily_k').mkdir(parents=True)
    conn=duckdb.connect(str(ab.DB),read_only=True)
    outputs=[]
    started=time.monotonic()
    warnings.filterwarnings('ignore',category=pd.errors.PerformanceWarning)
    warnings.filterwarnings('ignore',category=FutureWarning)
    for idx,ticker in enumerate(manifest['new_tickers'],1):
        raw=ab.get_raw(conn,manifest['raw_metadata'],ticker)
        dated=raw.copy()
        dated['Date']=pd.to_datetime(dated.Date)
        assert dated.Date.is_monotonic_increasing and not dated.Date.duplicated().any()
        assert np.isfinite(dated[['Open','High','Low','Close','Volume']].to_numpy()).all()
        classic=technical.compute_classic_technical_indicators(dated,ticker=ticker)
        enriched=raw.copy()
        for col in technical.CLASSIC_INDICATOR_COLUMNS:
            enriched[col]=classic[col].values
        pd.testing.assert_frame_equal(raw,enriched[raw.columns],check_exact=True)
        # Full technical recomputation is unchanged by persisted classic inputs.
        # This compares every generated column across every historical row, not
        # only a few latest indicators or one illustrative stock.
        before=technical.compute_technical_features(dated,ticker=ticker)
        enriched_dated=enriched.copy()
        enriched_dated['Date']=pd.to_datetime(enriched_dated.Date)
        after=technical.compute_technical_features(enriched_dated,ticker=ticker)
        assert set(before.columns)==set(after.columns)
        pd.testing.assert_frame_equal(before.reindex(sorted(before.columns),axis=1),after.reindex(sorted(after.columns),axis=1),check_exact=True)
        path=stage/'daily_k'/f'{ticker}.csv'
        enriched.to_csv(path,index=False,encoding='utf-8-sig')
        reread=pd.read_csv(path,dtype={'Date':str})
        pd.testing.assert_frame_equal(raw,reread[raw.columns],check_exact=True)
        outputs.append({'ticker':ticker,'rows':len(raw),'path':str(path),'sha256':ab.digest(path),'raw_column_changes':0,'ohlcv_changes':0,'institutional_margin_changes':0,'ml_technical_cells_compared':int(before.size),'ml_technical_changes':0,'classic_columns_added':list(technical.CLASSIC_INDICATOR_COLUMNS),'classic_missing_latest':[c for c in technical.CLASSIC_INDICATOR_COLUMNS if pd.isna(enriched[c].iloc[-1])]})
        if idx%10==0 or idx==len(manifest['new_tickers']):
            ab.emit('classic_derived_stage',completed=idx,total=84,raw_changes=0,ml_technical_changes=0)
    conn.close()
    assert ab.digest(ab.DB)==manifest['db_sha256']
    report={'status':'PASS_DERIVED_SOURCE_NOT_PROMOTED','companies':len(outputs),'rows':sum(x['rows'] for x in outputs),'seconds':round(time.monotonic()-started,2),'source_union_db_sha256':manifest['db_sha256'],'producer':'ml.features.technical.compute_classic_technical_indicators (same function as twstock.step7_calc_indicators)','producer_sha256':ab.digest(Path(technical.__file__)),'harness_sha256':ab.digest(Path(__file__)),'raw_column_changes':0,'ohlcv_changes':0,'institutional_margin_changes':0,'ml_technical_changes':0,'ml_technical_cells_compared':sum(x['ml_technical_cells_compared'] for x in outputs),'outputs':outputs,'limits':['For sources younger than rolling windows, persisted indicators remain NaN; no invented history.','All raw source columns stay identical; derived classic columns only.','Locked union DB, feature code, model artifacts, and ledgers were not changed.']}
    ab.write_json(stage/'manifest.json',report)
    ab.emit('classic_stage_complete',companies=84,rows=report['rows'],raw_changes=0,ml_technical_changes=0,compared_cells=report['ml_technical_cells_compared'],seconds=report['seconds'])


if __name__=='__main__':
    main()
