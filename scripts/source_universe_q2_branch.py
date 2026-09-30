"""Research C: replace only the repaired Q2 bundle on verified universe B."""
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
import json
import os
import pickle
import time

import duckdb
import numpy as np
import pandas as pd

from scripts import source_universe_snapshot_ab as ab

CANDIDATE=Path('F:/stock/output/financial_q2_repaired_20260906/candidate')
EXPECTED={
    '季報財務/financial_2026Q2.csv':'68e5909cdd2763ee0db8ec2128e7e07704235c3598ff810d59eee0856ec63469',
    '季報財務/eps_2026Q2.csv':'39f0f4d2f280ae9323a3f133d538901b63e9420bda3b64502e2b81b14d854372',
    '資產負債/bs_2026Q2.csv':'a1356f4d8f5e0730feb1e7930059d5a33ee4624d1d9e47fffdadee36d6f018ff',
}


@contextmanager
def quarter_overlay(candidate):
    original=pd.read_csv
    redirects={(ab.BASE/rel).resolve():candidate/rel for rel in EXPECTED}
    def read(path,*args,**kwargs):
        try:
            redirected=redirects.get(Path(path).resolve(),path)
        except TypeError:
            redirected=path
        return original(redirected,*args,**kwargs)
    pd.read_csv=read
    try:
        yield
    finally:
        pd.read_csv=original


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Physical acceptance workspace only')
    from ml import dataset,predict
    from ml.features import fundamental,eps,balance_sheet
    import backend.features.market_regime as regime
    out=ab.OUT/'quarter_C'
    out.mkdir(exist_ok=False)
    baseline=ab.OUT/'verified_rerun'
    assert json.loads((baseline/'rerun_equivalence.json').read_text(encoding='utf-8'))['rerun_input_hashes_unchanged']
    manifest=json.loads((baseline/'frozen_manifest.json').read_text(encoding='utf-8'))
    ab.verify_files(manifest['files'])
    assert ab.digest(ab.DB)==manifest['db_sha256']
    cache=pickle.loads((baseline/'raw_feature_rows.pkl').read_bytes())
    eligible={ticker for ticker,row in cache.items() if row is not None}
    affected=set()
    sources=[]
    for rel,expected in EXPECTED.items():
        path=CANDIDATE/rel
        assert ab.digest(path)==expected
        old=pd.read_csv(ab.BASE/rel,dtype={'Ticker':str}).set_index('Ticker')
        new=pd.read_csv(path,dtype={'Ticker':str}).set_index('Ticker')
        assert old.index.is_unique and new.index.is_unique
        assert not set(old.index)-set(new.index), 'Do not delete previously reported retired companies'
        columns=sorted((set(old)&set(new))-{'Name','Market'})
        index=old.index.union(new.index)
        left,right=old.reindex(index)[columns],new.reindex(index)[columns]
        same=left.eq(right)|(left.isna()&right.isna())
        changed=set(index[~same.all(axis=1)]) | (set(old.index)^set(new.index))
        affected|=changed
        sources.append({'relative_path':rel,'before_sha256':ab.digest(ab.BASE/rel),'candidate_sha256':expected,'before_rows':len(old),'candidate_rows':len(new),'new_tickers':sorted(set(new.index)-set(old.index)),'changed_tickers':sorted(changed),'eligible_affected':sorted(changed&eligible),'compared_columns':columns})
    started=time.monotonic()
    frozen={'as_of':ab.ASOF,'knowledge_time':'2026-09-06','observation_date':'2026-09-07','sources':sources,'candidate_manifest_sha256':ab.digest(CANDIDATE.parent/'manifest.json'),'method_sha256':ab.digest(Path('F:/stock/docs/METHOD_q2_source_consistency_ab_20260906.md')),'harness_sha256':ab.digest(__file__),'union_db_sha256':manifest['db_sha256'],'affected_eligible':sorted(affected&eligible),'baseline_snapshot_sha256':ab.digest(baseline/'snapshot_B.pkl')}
    ab.write_json(out/'frozen_q2_manifest.json',frozen)
    # B and C have the exact same raw pool and indices. Reuse B's independently
    # computed complete market series, then execute the original date join for C.
    market=pd.read_csv(baseline/'market_regime_B.csv',parse_dates=['Date'])
    for col in dataset.REGIME_FEATURE_COLS:
        market[col]=market[col].astype(np.float32)
    original_regime=regime.build_market_regime_features
    original_loader=dataset.load_single_stock
    original_list=dataset._list_daily_tickers
    regime.build_market_regime_features=lambda *args,**kwargs:market.copy(deep=True)
    conn=duckdb.connect(str(ab.DB),read_only=True)
    original_fingerprints={t:ab.fingerprint(row) for t,row in cache.items() if row is not None}
    try:
        fundamental._FINANCIAL_CACHE.clear()
        eps._EPS_CACHE.clear()
        balance_sheet._BS_CACHE.clear()
        with ab.duckdb_daily_reader(conn,manifest['raw_metadata']),quarter_overlay(CANDIDATE):
            twii=dataset._load_twii()
            for number,ticker in enumerate(sorted(affected&eligible),1):
                featured=original_loader(ticker,twii_df=twii,eligibility_mode='latest')
                assert featured is not None and not featured.empty
                cache[ticker]=featured.iloc[[-1]].drop(columns=dataset.REGIME_FEATURE_COLS,errors='ignore').copy()
                if number%10==0 or number==len(affected&eligible):
                    ab.emit('quarter_C_raw_features',completed=number,total=len(affected&eligible),seconds=round(time.monotonic()-started,1))
            dataset._list_daily_tickers=lambda:manifest['union_universe']
            dataset.load_single_stock=lambda ticker,**kwargs:None if cache[ticker] is None else cache[ticker].copy()
            snapshot=dataset.build_latest_snapshot(verbose=False)
            snapshot.to_pickle(out/'snapshot_C.pkl')
            provider=predict.build_latest_snapshot
            predict.build_latest_snapshot=lambda **kwargs:snapshot.copy(deep=True)
            try:
                predictions,meta=predict.predict_all(top_n=30,model_slot='production',save_snapshot=False)
            finally:
                predict.build_latest_snapshot=provider
            predictions.to_csv(out/f'predictions_C_{ab.ASOF}.csv',index=False,encoding='utf-8-sig')
            top=predict.apply_sector_cap(predictions,top_n=30)
            top.to_csv(out/'top30_capped_C.csv',index=False,encoding='utf-8-sig')
    finally:
        conn.close()
        dataset._list_daily_tickers=original_list
        dataset.load_single_stock=original_loader
        regime.build_market_regime_features=original_regime
    final_fingerprints={t:ab.fingerprint(row) for t,row in cache.items() if row is not None}
    changed_raw={t for t in original_fingerprints if original_fingerprints[t]!=final_fingerprints[t]}
    assert changed_raw.issubset(affected&eligible)
    (out/'raw_feature_rows_C.pkl').write_bytes(pickle.dumps(cache,protocol=pickle.HIGHEST_PROTOCOL))
    before=pd.read_csv(baseline/f'predictions_B_{ab.ASOF}.csv',dtype={'ticker':str})
    joined=before.merge(predictions,on=['ticker','date'],suffixes=('_B','_C'),validate='one_to_one')
    assert len(joined)==len(before)==len(predictions)
    deltas={}
    for col in ['prob_edge','pred_return_20d','up_prob','risk_adjusted_return']:
        delta=joined[col+'_C']-joined[col+'_B']
        joined[col+'_delta']=delta
        deltas[col]={'mean':float(delta.mean()),'max_abs':float(delta.abs().max()),'p95_abs':float(delta.abs().quantile(.95)),'nonzero_rows':int(delta.ne(0).sum()),'largest':joined.loc[delta.abs().nlargest(15).index,['ticker','date',col+'_B',col+'_C',col+'_delta']].to_dict('records')}
    joined.to_csv(out/'prediction_deltas_B_C.csv',index=False,encoding='utf-8-sig')
    changes=joined[joined.recommendation_B!=joined.recommendation_C][['ticker','date','recommendation_B','recommendation_C']]
    finite={col:bool(np.isfinite(predictions[col]).all()) for col in ['prob_edge','pred_return_20d','up_prob','flat_prob','down_prob']}
    assert all(finite.values())
    ab.verify_files(manifest['files'])
    for rel,expected in EXPECTED.items():
        assert ab.digest(CANDIDATE/rel)==expected
    assert ab.digest(ab.DB)==manifest['db_sha256']
    report={'status':'RESEARCH_Q2_C_COMPLETE_NOT_PROMOTION','as_of':ab.ASOF,'knowledge_time':'2026-09-06','observation_date':'2026-09-07','completed':datetime.now().isoformat(),'seconds':round(time.monotonic()-started,2),'quarter_source_changes':sources,'affected_eligible_recomputed':sorted(affected&eligible),'raw_feature_changed_tickers':sorted(changed_raw),'unaffected_cache_exact':True,'existing_join':len(joined),'finite_predictions':finite,'prediction_deltas':deltas,'recommendation_changes':changes.to_dict('records'),'rank_top30':{'B':before.head(30).ticker.tolist(),'C':predictions.head(30).ticker.tolist()},'capped_buy_top30_C':top.ticker.tolist(),'B_market_series_sha256':ab.digest(baseline/'market_regime_B.csv'),'market_series_reuse_basis':'Same frozen raw pool2031,unionDB,indices and calculation; original date join still applied.','limits':['Q2 source and parser reconciliation only; not historical performance evidence or proof of 9/4 knowledge.','No model or strategy threshold changes; no source promotion, ledgers, training or mail.']}
    ab.write_json(out/'q2_C_report.json',report)
    ab.emit('quarter_C_complete',rows=len(predictions),affected=len(affected&eligible),raw_changed=len(changed_raw),recommendation_changes=len(changes),finite=finite)


if __name__=='__main__':
    main()
