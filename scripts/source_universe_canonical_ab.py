"""Score frozen A/B snapshots with DataA and inspect canonical candidate changes.

Research outputs only; use through the physical acceptance workspace runner.
"""
from __future__ import annotations

from functools import lru_cache
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts import source_universe_snapshot_ab as ab


@contextmanager
def sector_source(generator, producer, tickers, destination):
    """Run the unchanged sector producer on this branch's full raw pool."""
    original_glob = Path.glob
    original_read = Path.read_text
    original_out = producer.OUT
    canonical_path = generator.BASE_DIR/'ml/data/sector_strength.json'
    def scoped_glob(path, pattern, *args, **kwargs):
        if path.resolve() == ab.DAILY.resolve() and pattern == '*.csv':
            return iter(ab.DAILY/f'{ticker}.csv' for ticker in tickers)
        return original_glob(path, pattern, *args, **kwargs)
    def scoped_read(path, *args, **kwargs):
        if path.resolve() == canonical_path.resolve():
            return original_read(destination, *args, **kwargs)
        return original_read(path, *args, **kwargs)
    try:
        Path.glob = scoped_glob
        producer.OUT = destination
        producer.main()
        Path.read_text = scoped_read
        yield
    finally:
        Path.glob = original_glob
        Path.read_text = original_read
        producer.OUT = original_out


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve() != ab.BASE:
        raise RuntimeError('Run only through the acceptance guard')
    from scripts import shadow_dataA_tracker as shadow
    from scripts import generate_entry_candidates as generator
    from scripts import sector_strength
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot-dir', type=Path, default=ab.OUT)
    args = parser.parse_args()
    input_dir = args.snapshot_dir
    manifest=json.loads((input_dir/'frozen_manifest.json').read_text(encoding='utf-8'))
    ab.verify_files(manifest['files'])
    context=json.loads((ab.OUT/'supplemental_context_manifest.json').read_text(encoding='utf-8'))
    ab.verify_files(context['files'])
    stage=ab.OUT/'enriched_v3_classic'
    classic_manifest=json.loads((stage/'manifest.json').read_text(encoding='utf-8'))
    assert classic_manifest['source_union_db_sha256']==manifest['db_sha256']
    assert classic_manifest['raw_column_changes']==0 and classic_manifest['ml_technical_changes']==0
    ab.verify_files(classic_manifest['outputs'])
    if (ab.OUT/'canonical_ab_report.json').exists():
        raise FileExistsError('Do not overwrite completed canonical A/B evidence')
    started=time.monotonic()
    meta=json.loads(shadow.SHADOW_META.read_text(encoding='utf-8'))
    model_path=ab.BASE/'ml/models'/Path(meta['model_file']).name
    booster=lgb.Booster(model_file=str(model_path))
    conn=duckdb.connect(str(ab.DB),read_only=True)
    original_technical=generator._technical
    original_daily=generator.DAILY_DIR
    cached_technical=lru_cache(maxsize=None)(original_technical)
    new_tickers=set(manifest['new_tickers'])
    @lru_cache(maxsize=None)
    def complete_technical(ticker,as_of=None):
        if ticker not in new_tickers:
            return cached_technical(ticker,as_of)
        generator.DAILY_DIR=stage/'daily_k'
        try:
            return original_technical(ticker,as_of)
        finally:
            generator.DAILY_DIR=original_daily
    original_models=generator.MODEL_DIR
    predictions={}
    plans={}
    before_plan=None
    technical_availability=[]
    with ab.duckdb_daily_reader(conn,manifest['raw_metadata']):
        try:
            for branch in ['A','B']:
                snapshot=pd.read_pickle(input_dir/f'snapshot_{branch}.pkl')
                scores=shadow.score_snapshot(snapshot,meta,booster)
                branch_sources=ab.OUT/f'canonical_sources_{branch}'
                branch_sources.mkdir(exist_ok=False)
                scores.to_csv(branch_sources/f'dataA_predictions_{ab.ASOF}.csv',index=False,encoding='utf-8-sig')
                snapshot.to_pickle(branch_sources/'snapshot_cache.pkl')
                generator.MODEL_DIR=branch_sources
                raw_tickers = [ticker for ticker,item in manifest['raw_metadata'].items() if branch == 'B' or item['branch'] == 'existing']
                with sector_source(generator, sector_strength, raw_tickers, branch_sources/'sector_strength.json'):
                    if branch=='A':
                        # New names have no daily source in the original branch.
                        generator._technical=lambda ticker,as_of=None: None if ticker in new_tickers else cached_technical(ticker,as_of)
                    else:
                        generator._technical=cached_technical
                        before_plan=generator.generate(as_of=ab.ASOF,trade_date='2026-09-07',persist_watchlist=False,include_live=False)
                        ab.write_json(ab.OUT/'canonical_entry_candidates_B_missing_classic.json',before_plan)
                        generator._technical=complete_technical
                    plan=generator.generate(as_of=ab.ASOF,trade_date='2026-09-07',persist_watchlist=False,include_live=False)
                ab.write_json(ab.OUT/f'canonical_entry_candidates_{branch}.json',plan)
                predictions[branch]=scores
                plans[branch]=plan
                rere=[row['ticker'] for row in plan['rows'] if row.get('lane')=='rere']
                ab.emit('canonical_branch_complete',branch=branch,model_rows=len(scores),candidate_rows=len(plan['rows']),rere_tickers=rere,source_path=str(branch_sources))
            required=pd.Timestamp(ab.ASOF).date()
            for ticker in sorted(new_tickers):
                technical_availability.append({'ticker':ticker,'before_classic_available':cached_technical(ticker,required) is not None,'after_classic_available':complete_technical(ticker,required) is not None})
        finally:
            generator.MODEL_DIR=original_models
            generator._technical=original_technical
            generator.DAILY_DIR=original_daily
    conn.close()
    joined=predictions['A'].merge(predictions['B'],on=['ticker','source_date'],suffixes=('_A','_B'),validate='one_to_one')
    delta=joined.pred_return_20d_B-joined.pred_return_20d_A
    joined['pred_return_20d_delta']=delta
    joined.to_csv(ab.OUT/'dataA_prediction_deltas.csv',index=False,encoding='utf-8-sig')
    row_keys=['ticker','lane','ptype','kind','status','source_date','model_source_date','pred20','score','data_status']
    membership={branch:[{key:row.get(key) for key in row_keys} for row in plans[branch]['rows']] for branch in ['A','B']}
    previous={row['ticker']:row for row in plans['A']['rows']}
    current={row['ticker']:row for row in plans['B']['rows']}
    changed=[]
    for ticker in sorted(set(previous)&set(current)):
        cols={key:{'A':previous[ticker].get(key),'B':current[ticker].get(key)} for key in row_keys if previous[ticker].get(key)!=current[ticker].get(key)}
        if cols:
            changed.append({'ticker':ticker,'fields':cols})
    ab.verify_files(manifest['files'])
    ab.verify_files(context['files'])
    ab.verify_files(classic_manifest['outputs'])
    assert ab.digest(ab.DB)==manifest['db_sha256']
    report={'as_of':ab.ASOF,'trade_date':'2026-09-07','status':'RESEARCH_CANONICAL_AB_COMPLETE_NOT_PROMOTION','seconds':round(time.monotonic()-started,2),'dataA_model_file':str(model_path),'dataA_model_sha256':ab.digest(model_path),'dataA_meta_sha256':ab.digest(shadow.SHADOW_META),'generator_sha256':ab.digest(Path(generator.__file__)),'harness_sha256':ab.digest(Path(__file__)),'source_universe_script_sha256':ab.digest(Path(ab.__file__)),'input_hashes_unchanged':True,'dataA_existing_join':len(joined),'dataA_delta':{'mean':float(delta.mean()),'min':float(delta.min()),'max':float(delta.max()),'max_abs':float(delta.abs().max()),'p95_abs':float(delta.abs().quantile(.95)),'nonzero_rows':int(delta.ne(0).sum())},'dataA_top30':{branch:scores[scores.source_date==ab.ASOF].sort_values('pred_return_20d',ascending=False).head(30).ticker.tolist() for branch,scores in predictions.items()},'candidate_rows':membership,'candidate_added':sorted(set(current)-set(previous)),'candidate_removed':sorted(set(previous)-set(current)),'shared_candidate_changes':changed,'rere_tickers':{branch:[row['ticker'] for row in plans[branch]['rows'] if row.get('lane')=='rere'] for branch in ['A','B']},'technical_cache':cached_technical.cache_info()._asdict(),'limits':['Canonical model-confirmation and rere system-proxy observations; not KOL personally endorsed picks or executed positions.','Frozen per-branch snapshot/model sources; common DuckDB raw technical source; no live data or watchlist persistence.','Unknown source inputs remain disclosed by the parent A/B coverage report; no weight, gate, or model changes.','No ledger creation, source promotion, model training, email, UI publication, or execution.']}
    report['supplemental_context']={'files':len(context['files']),'sha256':ab.digest(ab.OUT/'supplemental_context_manifest.json'),'unchanged':True}
    sectors = {branch:json.loads((ab.OUT/f'canonical_sources_{branch}/sector_strength.json').read_text(encoding='utf-8')) for branch in ['A','B']}
    formal_sector = json.loads((ab.BASE/'ml/data/sector_strength.json').read_text(encoding='utf-8'))
    report['sector_strength'] = {'producer_sha256':ab.digest(Path(sector_strength.__file__)), 'branch_raw_pool':{'A':manifest['old_daily_csv'],'B':len(manifest['raw_metadata'])}, 'same_close_formula':True, 'branch_outputs':sectors, 'formal_to_A_changed_sectors':[s for s in sorted(set(formal_sector)|set(sectors['A'])) if formal_sector.get(s)!=sectors['A'].get(s)], 'A_to_B_changed_sectors':[s for s in sorted(set(sectors['A'])|set(sectors['B'])) if sectors['A'].get(s)!=sectors['B'].get(s)], 'formal_artifact_sha256':ab.digest(ab.BASE/'ml/data/sector_strength.json')}
    report['classic_source_stage']={'path':str(stage),'manifest_sha256':ab.digest(stage/'manifest.json'),'raw_changes':0,'ml_technical_changes':0,'technical_availability':technical_availability}
    before_names={row['ticker'] for row in before_plan['rows']}
    report['B_before_classic_candidate_tickers']=[row['ticker'] for row in before_plan['rows']]
    report['B_classic_repair_added_candidates']=sorted(set(current)-before_names)
    report['B_classic_repair_removed_candidates']=sorted(before_names-set(current))
    ab.write_json(ab.OUT/'canonical_ab_report.json',report)
    ab.emit('canonical_ab_complete',added=report['candidate_added'],removed=report['candidate_removed'],rere_tickers=report['rere_tickers'],dataA_delta=report['dataA_delta'])


if __name__=='__main__':
    main()
