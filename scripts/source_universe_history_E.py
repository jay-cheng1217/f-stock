"""History E: validate C's BS baseline, replace verified BS outputs, infer."""
from pathlib import Path
from datetime import datetime
import json
import os
import pickle
import time

import numpy as np
import pandas as pd

from scripts import source_universe_snapshot_ab as ab

SOURCES=Path('F:/stock/output/bs_historical_latest_E_20260906')
FINAL_SOURCES=Path('F:/stock/output/bs_retained_latest_F_20260906')


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Physical acceptance workspace only')
    from ml import dataset,predict
    from ml.features.balance_sheet import compute_balance_sheet_features,BALANCE_SHEET_FEATURE_COLS
    import backend.features.market_regime as regime
    baseline=ab.OUT/'quarter_C'
    assert json.loads((baseline/'q2_C_report.json').read_text(encoding='utf-8'))['status']=='RESEARCH_Q2_C_COMPLETE_NOT_PROMOTION'
    out=ab.OUT/'history_E'
    out.mkdir(exist_ok=False)
    source_manifest=json.loads((SOURCES/'manifest.json').read_text(encoding='utf-8'))
    ab.verify_files(source_manifest['sources'])
    final_manifest=json.loads((FINAL_SOURCES/'manifest.json').read_text(encoding='utf-8'))
    ab.verify_files(final_manifest['sources'])
    input_manifest=json.loads((ab.OUT/'verified_rerun/frozen_manifest.json').read_text(encoding='utf-8'))
    ab.verify_files(input_manifest['files'])
    # Every financial file must match C's precise bundle, not a new source mix.
    from scripts.source_universe_q2_branch import CANDIDATE,EXPECTED
    for path in (SOURCES/'financial').glob('*.csv'):
        expected_path=CANDIDATE/'季報財務'/path.name if path.name=='financial_2026Q2.csv' else ab.BASE/'季報財務'/path.name
        assert ab.digest(path)==ab.digest(expected_path),str(path)
    cache=pickle.loads((baseline/'raw_feature_rows_C.pkl').read_bytes())
    before_fingerprints={ticker:ab.fingerprint(row) for ticker,row in cache.items() if row is not None}
    changes=[]
    final_changes=[]
    started=time.monotonic()
    for number,(ticker,row) in enumerate([(t,r) for t,r in cache.items() if r is not None],1):
        daily=row[['Date','Close']].copy()
        before=compute_balance_sheet_features(daily,ticker,str(SOURCES/'before/資產負債'),str(SOURCES/'financial'))
        after=compute_balance_sheet_features(daily,ticker,str(SOURCES/'after/資產負債'),str(SOURCES/'financial'))
        final=compute_balance_sheet_features(daily,ticker,str(FINAL_SOURCES/'after/資產負債'),str(SOURCES/'financial'))
        for column in BALANCE_SHEET_FEATURE_COLS:
            actual=row[column].iloc[0] if column in row else np.nan
            expected=before[column].iloc[0] if column in before else np.nan
            replacement=after[column].iloc[0] if column in after else np.nan
            final_value=final[column].iloc[0] if column in final else np.nan
            if not ((pd.isna(replacement) and pd.isna(final_value)) or replacement==final_value):
                final_changes.append({'ticker':ticker,'feature':column,'E':replacement,'F':final_value})
            assert (pd.isna(actual) and pd.isna(expected)) or actual==expected,(ticker,column,actual,expected)
            if (pd.isna(actual) and pd.isna(replacement)) or actual==replacement:
                continue
            changes.append({'ticker':ticker,'date':str(row.Date.iloc[0]),'feature':column,'C':float(actual) if pd.notna(actual) else None,'E':float(replacement) if pd.notna(replacement) else None})
            cache[ticker]=cache[ticker].copy()
            cache[ticker].loc[:,column]=replacement
        if number%200==0:
            ab.emit('history_E_baseline_verified',rows=number,changed_cells=len(changes),seconds=round(time.monotonic()-started,1))
    frozen={'branch':'E','baseline':'C','sources':source_manifest['sources'],'source_manifest_sha256':ab.digest(SOURCES/'manifest.json'),'baseline_snapshot_sha256':ab.digest(baseline/'snapshot_C.pkl'),'baseline_raw_cache_sha256':ab.digest(baseline/'raw_feature_rows_C.pkl'),'harness_sha256':ab.digest(__file__),'union_db_sha256':input_manifest['db_sha256'],'all_baseline_BS_cells_exact':len(before_fingerprints)*len(BALANCE_SHEET_FEATURE_COLS)}
    ab.write_json(out/'frozen_E_manifest.json',frozen)
    market=pd.read_csv(ab.OUT/'verified_rerun/market_regime_B.csv',parse_dates=['Date'])
    for column in dataset.REGIME_FEATURE_COLS:
        market[column]=market[column].astype(np.float32)
    original_regime=regime.build_market_regime_features
    original_loader,original_list=dataset.load_single_stock,dataset._list_daily_tickers
    provider=predict.build_latest_snapshot
    try:
        regime.build_market_regime_features=lambda *args,**kwargs:market.copy(deep=True)
        dataset._list_daily_tickers=lambda:input_manifest['union_universe']
        dataset.load_single_stock=lambda ticker,**kwargs:None if cache[ticker] is None else cache[ticker].copy()
        snapshot=dataset.build_latest_snapshot(verbose=False)
        snapshot.to_pickle(out/'snapshot_E.pkl')
        predict.build_latest_snapshot=lambda **kwargs:snapshot.copy(deep=True)
        predictions,_=predict.predict_all(top_n=30,model_slot='production',save_snapshot=False)
        predictions.to_csv(out/f'predictions_E_{ab.ASOF}.csv',index=False,encoding='utf-8-sig')
        top=predict.apply_sector_cap(predictions,top_n=30)
        top.to_csv(out/'top30_capped_E.csv',index=False,encoding='utf-8-sig')
    finally:
        regime.build_market_regime_features=original_regime
        dataset.load_single_stock,dataset._list_daily_tickers=original_loader,original_list
        predict.build_latest_snapshot=provider
    (out/'raw_feature_rows_E.pkl').write_bytes(pickle.dumps(cache,protocol=pickle.HIGHEST_PROTOCOL))
    before=pd.read_csv(baseline/f'predictions_C_{ab.ASOF}.csv',dtype={'ticker':str})
    joined=before.merge(predictions,on=['ticker','date'],suffixes=('_C','_E'),validate='one_to_one')
    assert len(joined)==len(before)==len(predictions)
    deltas={}
    for column in ['prob_edge','pred_return_20d','up_prob','risk_adjusted_return']:
        delta=joined[column+'_E']-joined[column+'_C']
        joined[column+'_delta']=delta
        deltas[column]={'mean':float(delta.mean()),'max_abs':float(delta.abs().max()),'p95_abs':float(delta.abs().quantile(.95)),'nonzero_rows':int(delta.ne(0).sum())}
    joined.to_csv(out/'prediction_deltas_C_E.csv',index=False,encoding='utf-8-sig')
    rec_changes=joined[joined.recommendation_C.ne(joined.recommendation_E)][['ticker','date','recommendation_C','recommendation_E']]
    changed_tickers={item['ticker'] for item in changes}
    assert all(ab.fingerprint(cache[t])==fp for t,fp in before_fingerprints.items() if t not in changed_tickers)
    finite={column:bool(np.isfinite(predictions[column]).all()) for column in ['prob_edge','pred_return_20d','up_prob','flat_prob','down_prob']}
    assert all(finite.values())
    ab.verify_files(source_manifest['sources'])
    ab.verify_files(final_manifest['sources'])
    ab.verify_files(input_manifest['files'])
    report={'status':'RESEARCH_HISTORY_E_COMPLETE_NOT_PROMOTION','baseline':'C','as_of':ab.ASOF,'knowledge_time':'2026-09-06','observation_date':'2026-09-07','completed':datetime.now().isoformat(),'seconds':round(time.monotonic()-started,2),'baseline_BS_cells_exact':frozen['all_baseline_BS_cells_exact'],'changed_raw_tickers':sorted(changed_tickers),'changed_raw_cells':changes,'unaffected_cache_exact':True,'prediction_deltas':deltas,'recommendation_changes':rec_changes.to_dict('records'),'rank_top30':{'C':before.head(30).ticker.tolist(),'E':predictions.head(30).ticker.tolist()},'capped_buy_top30_E':top.ticker.tolist(),'finite_predictions':finite,'limits':['E only changes the 25 pre-Q2 BS sources on verified C; retired-source F is a separate branch.','Baseline verified all8 canonical BS fields for all eligible stocks before replacement; full cross-sectional processing/pinned inference follows.','No training, production source promotion, ledger changes or mail.']}
    ab.write_json(out/'history_E_report.json',report)
    ab.write_json(out/'retained_F_eligibility_equivalence.json',{'status':'F_MODEL_INPUT_EXACT_ZERO' if not final_changes else 'F_ADDITIONAL_INFERENCE_REQUIRED','eligible_rows':len(before_fingerprints),'canonical_BS_cells_compared':len(before_fingerprints)*len(BALANCE_SHEET_FEATURE_COLS),'changed_cells':final_changes,'source_manifest_sha256':ab.digest(FINAL_SOURCES/'manifest.json'),'all_pool_agent_changed_tickers':final_manifest.get('changed_tickers'),'model_rerun_omitted_only_if_exact_zero':not final_changes,'note':'2867 is in the broad canonical stock universe but fails latest eligibility; 5371 is eligible and is explicitly compared. No assumption that all retired-looking names are excluded.'})
    ab.emit('history_E_complete',changed_tickers=len(changed_tickers),changed_cells=len(changes),recommendation_changes=len(rec_changes),deltas=deltas)


if __name__=='__main__':
    main()
