"""Canonical DataA/entry follow-through for Q2 branch C; no ledger writes."""
from functools import lru_cache
from contextlib import contextmanager
from pathlib import Path
import argparse
import json
import os

import duckdb
import lightgbm as lgb
import pandas as pd

from scripts import source_universe_snapshot_ab as ab


@contextmanager
def reuse_common_sector(generator, destination):
    """Quarterly sources do not affect the verified B Close/sector inputs."""
    source=ab.OUT/'canonical_sources_B/sector_strength.json'
    destination.write_bytes(source.read_bytes())
    original=Path.read_text
    target=generator.BASE_DIR/'ml/data/sector_strength.json'
    def read(path,*args,**kwargs):
        return original(destination if path.resolve()==target.resolve() else path,*args,**kwargs)
    Path.read_text=read
    try:
        yield
    finally:
        Path.read_text=original


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Physical acceptance workspace only')
    from scripts import generate_entry_candidates as generator
    from scripts import shadow_dataA_tracker as shadow
    from scripts import sector_strength
    parser=argparse.ArgumentParser()
    parser.add_argument('--branch',choices=['C','E'],default='C')
    args=parser.parse_args()
    branch=args.branch
    previous_branch='B' if branch=='C' else 'C'
    out=ab.OUT/('quarter_C' if branch=='C' else 'history_E')
    previous_out=ab.OUT if branch=='C' else ab.OUT/'quarter_C'
    stage_report=out/('q2_C_report.json' if branch=='C' else 'history_E_report.json')
    assert 'COMPLETE_NOT_PROMOTION' in json.loads(stage_report.read_text(encoding='utf-8'))['status']
    manifest=json.loads((ab.OUT/'verified_rerun/frozen_manifest.json').read_text(encoding='utf-8'))
    ab.verify_files(manifest['files'])
    branch_sources=out/f'canonical_sources_{branch}'
    branch_sources.mkdir(exist_ok=False)
    meta=json.loads(shadow.SHADOW_META.read_text(encoding='utf-8'))
    model=ab.BASE/'ml/models'/Path(meta['model_file']).name
    booster=lgb.Booster(model_file=str(model))
    snapshot=pd.read_pickle(out/f'snapshot_{branch}.pkl')
    scores=shadow.score_snapshot(snapshot,meta,booster)
    scores.to_csv(branch_sources/f'dataA_predictions_{ab.ASOF}.csv',index=False,encoding='utf-8-sig')
    snapshot.to_pickle(branch_sources/'snapshot_cache.pkl')
    original_model=generator.MODEL_DIR
    original_daily=generator.DAILY_DIR
    original_technical=generator._technical
    new=set(manifest['new_tickers'])
    @lru_cache(maxsize=None)
    def technical(ticker,as_of=None):
        if ticker in new:
            generator.DAILY_DIR=ab.OUT/'enriched_v3_classic/daily_k'
        try:
            return original_technical(ticker,as_of)
        finally:
            generator.DAILY_DIR=original_daily
    try:
        generator.MODEL_DIR=branch_sources
        generator._technical=technical
        # prepare() already proved every frozen CSV value/dtype equals unionDB;
        # reading these unchanged files avoids repeated full-row DuckDB scans.
        with reuse_common_sector(generator,branch_sources/'sector_strength.json'):
            plan=generator.generate(as_of=ab.ASOF,trade_date='2026-09-07',persist_watchlist=False,include_live=False)
    finally:
        generator.MODEL_DIR=original_model
        generator.DAILY_DIR=original_daily
        generator._technical=original_technical
    b_sector=json.loads((previous_out/f'canonical_sources_{previous_branch}/sector_strength.json').read_text(encoding='utf-8'))
    c_sector=json.loads((branch_sources/'sector_strength.json').read_text(encoding='utf-8'))
    assert b_sector==c_sector
    ab.write_json(out/f'canonical_entry_candidates_{branch}.json',plan)
    previous=json.loads((previous_out/f'canonical_entry_candidates_{previous_branch}.json').read_text(encoding='utf-8'))
    b_scores=pd.read_csv(previous_out/f'canonical_sources_{previous_branch}/dataA_predictions_{ab.ASOF}.csv',dtype={'ticker':str})
    joined=b_scores.merge(scores,on=['ticker','source_date'],suffixes=(f'_{previous_branch}',f'_{branch}'),validate='one_to_one')
    assert len(joined)==len(scores)
    delta=joined[f'pred_return_20d_{branch}']-joined[f'pred_return_20d_{previous_branch}']
    joined['delta']=delta
    joined.to_csv(out/f'dataA_deltas_{previous_branch}_{branch}.csv',index=False,encoding='utf-8-sig')
    before={row['ticker']:row for row in previous['rows']}
    after={row['ticker']:row for row in plan['rows']}
    changes=[]
    keys=['lane','kind','ptype','score','pred20','source_date','model_source_date','data_status']
    for ticker in sorted(set(before)&set(after)):
        fields={key:{previous_branch:before[ticker].get(key),branch:after[ticker].get(key)} for key in keys if before[ticker].get(key)!=after[ticker].get(key)}
        if fields:
            changes.append({'ticker':ticker,'changes':fields})
    report={'status':'RESEARCH_CANONICAL_C_COMPLETE_NOT_PROMOTION','as_of':ab.ASOF,'knowledge_time':'2026-09-06','observation_date':'2026-09-07','model_sha256':ab.digest(model),'model_meta_sha256':ab.digest(shadow.SHADOW_META),'generator_sha256':ab.digest(Path(generator.__file__)),'candidate_added':sorted(set(after)-set(before)),'candidate_removed':sorted(set(before)-set(after)),'shared_changes':changes,'candidate_rows_C':plan['rows'],'rere_tickers':{'B':[r['ticker'] for r in previous['rows'] if r.get('lane')=='rere'],'C':[r['ticker'] for r in plan['rows'] if r.get('lane')=='rere']},'dataA_delta':{'max_abs':float(delta.abs().max()),'p95_abs':float(delta.abs().quantile(.95)),'nonzero':int(delta.ne(0).sum())},'dataA_top30_C':scores[scores.source_date==ab.ASOF].sort_values('pred_return_20d',ascending=False).head(30).ticker.tolist(),'sector_strength_B_C_exact':True,'no_gate_or_model_change':True,'no_watchlist_or_ledger_write':True}
    report['status']=f'RESEARCH_CANONICAL_{branch}_COMPLETE_NOT_PROMOTION'
    report['branch']=branch
    report['baseline']=previous_branch
    report['candidate_rows']=report.pop('candidate_rows_C')
    report['dataA_top30']=report.pop('dataA_top30_C')
    report['rere_tickers']={previous_branch:report['rere_tickers']['B'],branch:report['rere_tickers']['C']}
    report['sector_strength_baseline_exact']=report.pop('sector_strength_B_C_exact')
    report['sector_strength_reuse']={'source':str(ab.OUT/'canonical_sources_B/sector_strength.json'),'sha256':ab.digest(ab.OUT/'canonical_sources_B/sector_strength.json'),'basis':'Same frozen2031 raw Close series and sector mapping; quarterly BS/financial/EPS replacements do not enter the original sector_strength producer.'}
    report['technical_source']='Frozen original CSVs with exact DuckDB round-trip proof; new84 use verified v3 classic CSVs. Original _technical/as-of truncation unchanged.'
    ab.verify_files(manifest['files'])
    ab.write_json(out/f'canonical_{branch}_report.json',report)
    ab.emit(f'canonical_{branch}_complete',candidates=len(plan['rows']),added=report['candidate_added'],removed=report['candidate_removed'],rere=report['rere_tickers'],dataA_delta=report['dataA_delta'])


if __name__=='__main__':
    main()
