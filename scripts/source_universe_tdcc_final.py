"""Compare all eligible E TDCC inputs with the complete repaired D history."""
from pathlib import Path
import json
import os
import pickle
import time
import numpy as np
import pandas as pd
from scripts import source_universe_snapshot_ab as ab
from scripts.source_universe_verified_rerun import executable_ast


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Physical acceptance workspace only')
    from ml.features import tdcc
    from ml import dataset
    source=ab.BASE/'output/historical_gaps_20260906/tdcc_feature_ab_2031'
    evidence=json.loads((source/'result.json').read_text(encoding='utf-8'))
    before=ab.BASE/'集保分散/tdcc_summary.csv'
    after=source/'summary_after.csv'
    assert ab.digest(before)==evidence['summary_before_sha256']
    assert ab.digest(after)==evidence['summary_after_sha256']
    assert executable_ast(Path(tdcc.__file__).read_bytes())==executable_ast(Path('F:/stock/ml/features/tdcc.py').read_bytes())
    cache=pickle.loads((ab.OUT/'history_E/raw_feature_rows_E.pkl').read_bytes())
    columns=tdcc.TDCC_FEATURE_COLS
    started=time.monotonic();changes=[];rows=0
    tdcc._TDCC_CACHE.clear()
    for ticker,row in cache.items():
        if row is None:
            continue
        daily=row[['Date','Close']].copy()
        a=tdcc.compute_tdcc_features(daily,ticker,str(before))
        b=tdcc.compute_tdcc_features(daily,ticker,str(after))
        for column in columns:
            baseline=row[column].iloc[0] if column in row else np.nan
            actual=a[column].iloc[0] if column in a else np.nan
            updated=b[column].iloc[0] if column in b else np.nan
            assert (pd.isna(baseline) and pd.isna(actual)) or baseline==actual,(ticker,column,baseline,actual)
            if not ((pd.isna(actual) and pd.isna(updated)) or actual==updated):
                changes.append({'ticker':ticker,'date':str(row.Date.iloc[0]),'feature':column,'E':float(actual) if pd.notna(actual) else None,'D_final':float(updated) if pd.notna(updated) else None})
        rows+=1
        if rows%200==0:
            ab.emit('tdcc_E_baseline_verified',rows=rows,changed_cells=len(changes),seconds=round(time.monotonic()-started,1))
    assert ab.digest(before)==evidence['summary_before_sha256'] and ab.digest(after)==evidence['summary_after_sha256']
    inactive={ticker:dataset.diagnose_stock_eligibility(ticker) for ticker in sorted({item['ticker'] for item in evidence['changes']})}
    report={'status':'D_FINAL_ELIGIBLE_INPUT_EXACT_ZERO' if not changes else 'D_FINAL_INFERENCE_REQUIRED','as_of':ab.ASOF,'eligible_rows':rows,'feature_count':len(columns),'baseline_cells_exact':rows*len(columns),'changed_cells':changes,'all_pool_changed_cells':evidence['changed_cells'],'ineligible_change_diagnostics':inactive,'before_sha256':ab.digest(before),'after_sha256':ab.digest(after),'D_result_sha256':ab.digest(source/'result.json'),'E_raw_cache_sha256':ab.digest(ab.OUT/'history_E/raw_feature_rows_E.pkl'),'TDCC_executable_AST_matches_current':True,'frozen_code_sha256':ab.digest(Path(tdcc.__file__)),'current_code_sha256':ab.digest(Path('F:/stock/ml/features/tdcc.py')),'seconds':round(time.monotonic()-started,2),'model_rerun_omitted_only_if_exact_zero':not changes,'limits':['Entire historical TDCC prefix is used for every stock; no assumption that old dates or stale rows are excluded.','This proves final latest eligible model inputs, not unchanged historical training features or past returns.']}
    ab.write_json(ab.OUT/'history_E/tdcc_D_final_equivalence.json',report)
    ab.emit('tdcc_final_complete',rows=rows,features=len(columns),changed_cells=len(changes),status=report['status'])


if __name__=='__main__':
    main()
