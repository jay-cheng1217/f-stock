"""Research-only universe A/B using one DuckDB raw frame and frozen canonical code.

Run only inside the physical pipeline-acceptance copy through its existing guard.
This script never trains, writes model caches, creates portfolio entries, or sends mail.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import hashlib
import glob
import json
import os
from pathlib import Path
import pickle
import shutil
import time
import warnings

import duckdb
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / 'ml/reports/research/source_universe_ab_20260906'
DB = BASE / 'ab_union.duckdb'
DAILY = BASE / '日K資料'
ASOF = '2026-09-04'
RAW_INSTITUTIONAL = ['Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell', 'Margin_Balance', 'Short_Balance']
READ_CSV = pd.read_csv


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def emit(event, **values):
    print(json.dumps({'time': datetime.now().isoformat(), 'event': event, **values}, ensure_ascii=False, default=str), flush=True)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def fingerprint(frame):
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=False).values.tobytes()).hexdigest()


def verify_files(items):
    changed = [item['path'] for item in items if not Path(item['path']).is_file() or digest(item['path']) != item['sha256']]
    if changed:
        raise RuntimeError(f'Frozen inputs changed: {changed[:20]}')
    return len(items)


def frozen_files():
    original = json.loads((BASE.parent / 'input_manifest.json').read_text(encoding='utf-8'))
    paths = []
    for item in original['files']:
        rel = Path(item['path'])
        # Ledger / generated output files are not an inference source. Inputs below
        # include frozen feature code, factors, raw auxiliary data and all model pins.
        if rel.suffix == '.db' or str(rel).replace('\\', '/').startswith(('frontend/', 'ml/reports/', 'logs/')):
            continue
        path = BASE / rel
        if path.is_file():
            paths.append(path)
    contract = json.loads((BASE.parent / 'production_model_contract.json').read_text(encoding='utf-8'))
    # Explicit production pins may resolve outside the physical copy: read-only
    # hash them as well, without printing any environment or credential values.
    for detail in contract['validation']['details']:
        paths.extend(Path(detail[key]) for key in ('meta_path', 'model_path'))
    paths.extend([BASE.parent / 'production_model_contract.json', Path(__file__)])
    return [{'path': str(p), 'sha256': digest(p), 'bytes': p.stat().st_size} for p in sorted(set(paths))]


def get_raw(conn, metadata, ticker):
    item = metadata[ticker]
    columns = item['columns']
    sql = 'SELECT ' + ','.join(quote(c) for c in columns) + ' FROM daily_k WHERE _ab_ticker = ? ORDER BY _ab_row'
    frame = conn.execute(sql, [ticker]).fetchdf()
    for col, dtype in item['dtypes'].items():
        frame[col] = frame[col].astype(dtype)
    return frame


@contextmanager
def duckdb_daily_reader(conn, metadata):
    """Replace only canonical daily-CSV reads with value-identical DuckDB frames.

    All auxiliary files still use their existing readers in the frozen workspace.
    Restoring original dtypes avoids CSV floating-point serialization round trips.
    """
    def read_csv(path, *args, **kwargs):
        try:
            candidate = Path(path).resolve()
            is_daily = candidate.parent == DAILY.resolve() and candidate.stem in metadata
        except TypeError:
            is_daily = False
        if not is_daily:
            return READ_CSV(path, *args, **kwargs)
        if args:
            raise ValueError('Unexpected positional CSV options in canonical daily reader')
        allowed = {'dtype', 'usecols', 'encoding', 'nrows', 'low_memory', 'parse_dates'}
        if set(kwargs) - allowed:
            raise ValueError(f'Unexpected canonical CSV options: {set(kwargs) - allowed}')
        frame = get_raw(conn, metadata, candidate.stem)
        usecols = kwargs.get('usecols')
        if callable(usecols):
            frame = frame[[c for c in frame if usecols(c)]]
        elif usecols is not None:
            frame = frame[list(usecols)]
        dtype = kwargs.get('dtype')
        if isinstance(dtype, dict):
            frame = frame.astype({c: d for c, d in dtype.items() if c in frame})
        elif dtype is not None:
            frame = frame.astype(dtype)
        parse_dates = kwargs.get('parse_dates')
        if parse_dates:
            if not isinstance(parse_dates, list) or not all(isinstance(c, str) for c in parse_dates):
                raise ValueError('Unsupported canonical parse_dates option')
            for col in parse_dates:
                frame[col] = pd.to_datetime(frame[col])
        if kwargs.get('nrows') is not None:
            frame = frame.head(kwargs['nrows'])
        return frame.copy()
    pd.read_csv = read_csv
    try:
        yield
    finally:
        pd.read_csv = READ_CSV


@contextmanager
def branch_daily_enumeration(tickers):
    """Scope canonical global market breadth to this branch's complete raw pool."""
    original = glob.glob
    wanted = set(tickers)
    target = os.path.normcase(os.path.normpath(str(DAILY / '*.csv')))
    def branch_glob(pattern, *args, **kwargs):
        paths = original(pattern, *args, **kwargs)
        if os.path.normcase(os.path.normpath(str(pattern))) == target:
            return [p for p in paths if Path(p).stem in wanted]
        return paths
    glob.glob = branch_glob
    try:
        yield
    finally:
        glob.glob = original


def prepare(source_dir, source_manifest):
    import ml.dataset as dataset
    if DB.exists() or (OUT / 'frozen_manifest.json').exists():
        raise FileExistsError('Refusing to overwrite the union DB or frozen manifest')
    old_paths = sorted(p for p in DAILY.glob('*.csv') if p.stem.isdigit())
    old_universe = dataset._list_daily_tickers()
    new_paths = sorted(Path(source_dir).glob('*.csv'))
    assert len(new_paths) == 84, len(new_paths)
    assert not set(p.stem for p in old_paths) & set(p.stem for p in new_paths)
    source_manifest = Path(source_manifest).resolve()
    enrichment = json.loads(source_manifest.read_text(encoding='utf-8'))
    assert enrichment['companies'] == 84 and enrichment['rows'] == 16024
    assert enrichment['ohlcv_value_or_key_changes'] == 0
    assert not enrichment['source_errors'] and not enrichment['raw_merged_conflicts']
    expected_sources = {str(item['ticker']):item['sha256'] for item in enrichment['outputs']}
    assert {p.stem:digest(p) for p in new_paths} == expected_sources
    sources = frozen_files()
    sources += [{'path':str(p.resolve()), 'sha256':digest(p), 'bytes':p.stat().st_size} for p in new_paths + [source_manifest]]
    emit('inputs_frozen', files=len(sources), old_daily_csv=len(old_paths), old_active_universe=len(old_universe), added_csv=len(new_paths))
    conn = duckdb.connect(str(DB))
    conn.execute('SET threads=4')
    conn.execute('CREATE TABLE daily_k (_ab_ticker VARCHAR, _ab_row BIGINT)')
    schema = {'_ab_ticker', '_ab_row'}
    metadata = {}
    total_rows = 0
    try:
        for idx, path in enumerate(old_paths + new_paths, 1):
            frame = READ_CSV(path, dtype={'Date':str})
            frame = frame.loc[pd.to_datetime(frame['Date']).le(pd.Timestamp(ASOF))].reset_index(drop=True)
            assert not frame['Date'].duplicated().any(), path
            is_new = path in new_paths
            if is_new:
                for col in RAW_INSTITUTIONAL:
                    if col not in frame:
                        frame[col] = np.nan
            metadata[path.stem] = {'columns':list(frame.columns), 'dtypes':{c:str(frame[c].dtype) for c in frame}, 'rows':len(frame), 'source_sha256':digest(path), 'raw_fingerprint':fingerprint(frame), 'branch':'new' if is_new else 'existing'}
            for col in frame:
                if col not in schema:
                    dtype = 'DOUBLE' if pd.api.types.is_numeric_dtype(frame[col]) else 'VARCHAR'
                    conn.execute(f'ALTER TABLE daily_k ADD COLUMN {quote(col)} {dtype}')
                    schema.add(col)
            insertion = frame.copy()
            insertion['_ab_ticker'] = path.stem
            insertion['_ab_row'] = np.arange(len(frame), dtype=np.int64)
            conn.register('_ab_insert', insertion)
            conn.execute('INSERT INTO daily_k BY NAME SELECT * FROM _ab_insert')
            conn.unregister('_ab_insert')
            restored = get_raw(conn, metadata, path.stem)
            pd.testing.assert_frame_equal(frame, restored, check_exact=True, check_dtype=True)
            total_rows += len(frame)
            if idx % 200 == 0 or idx == len(old_paths) + len(new_paths):
                emit('duckdb_union', completed=idx, total=len(old_paths)+len(new_paths), rows=total_rows, exact_raw_roundtrip=True)
        conn.execute('CREATE INDEX ab_ticker_idx ON daily_k (_ab_ticker)')
        conn.execute('CHECKPOINT')
    finally:
        conn.close()
    # Add only the new source files to the physical copy. Reads during inference
    # are routed to the frozen DB; physical paths preserve canonical exists/listdir.
    for path in new_paths:
        shutil.copyfile(path, DAILY / path.name)
    union_universe = dataset._list_daily_tickers()
    assert set(union_universe) - set(old_universe) == set(p.stem for p in new_paths)
    frozen = {'as_of':ASOF, 'started':datetime.now().isoformat(), 'db_path':str(DB), 'db_sha256':digest(DB), 'raw_rows':total_rows, 'old_daily_csv':len(old_paths), 'old_universe':old_universe, 'union_universe':union_universe, 'new_tickers':[p.stem for p in new_paths], 'source_manifest':str(source_manifest), 'files':sources, 'raw_metadata':metadata, 'method':'One DuckDB raw frame; exact dtype/value roundtrip checked for every stock; canonical latest eligibility, last available row <= as-of; raw features once, canonical cross-sectional postprocessing separately.'}
    write_json(OUT / 'frozen_manifest.json', frozen)
    verify_files(sources)
    emit('prepare_complete', rows=total_rows, union_csv=len(metadata), db_sha256=frozen['db_sha256'])


def run():
    import ml.dataset as dataset
    import ml.predict as predict
    import backend.features.market_regime as market_regime
    manifest = json.loads((OUT/'frozen_manifest.json').read_text(encoding='utf-8'))
    verify_files(manifest['files'])
    assert digest(DB) == manifest['db_sha256']
    conn = duckdb.connect(str(DB), read_only=True)
    conn.execute('SET threads=4')
    checkpoint = OUT/'raw_feature_rows.pkl'
    cache = pickle.loads(checkpoint.read_bytes()) if checkpoint.exists() else {}
    original_loader = dataset.load_single_stock
    original_list = dataset._list_daily_tickers
    started = time.monotonic()
    errors = []
    with duckdb_daily_reader(conn, manifest['raw_metadata']):
        twii = dataset._load_twii()
        for idx, ticker in enumerate(manifest['union_universe'], 1):
            if ticker not in cache:
                try:
                    featured = original_loader(ticker, twii_df=twii, eligibility_mode='latest')
                    # These three columns depend on the whole raw source pool,
                    # not the stock. Never store B's regime in the shared cache.
                    cache[ticker] = None if featured is None or featured.empty else featured.iloc[[-1]].drop(columns=dataset.REGIME_FEATURE_COLS, errors='ignore').copy()
                except Exception as exc:
                    errors.append({'ticker':ticker,'error':repr(exc)})
                    write_json(OUT/'feature_errors.json', errors)
                    raise
            if idx % 25 == 0 or idx == len(manifest['union_universe']):
                checkpoint.write_bytes(pickle.dumps(cache, protocol=pickle.HIGHEST_PROTOCOL))
                emit('canonical_raw_features', completed=idx, total=len(manifest['union_universe']), eligible=sum(v is not None for v in cache.values()), elapsed_seconds=round(time.monotonic()-started,1))
        raw = pd.concat([v for v in cache.values() if v is not None], ignore_index=True)
        raw.to_pickle(OUT/'raw_snapshot_union.pkl')
        new_raw = raw[raw.ticker.isin(manifest['new_tickers'])]
        new_raw.to_csv(OUT/'new_eligible_raw_features.csv', index=False, encoding='utf-8-sig')
        raw_fingerprints = {ticker:fingerprint(frame) for ticker,frame in cache.items() if frame is not None}
        write_json(OUT/'raw_feature_fingerprints.json',raw_fingerprints)
        frames = {}
        for branch, universe in [('A',manifest['old_universe']),('B',manifest['union_universe'])]:
            # Reuse the original complete build_latest_snapshot function, replacing
            # only per-stock IO with already-computed rows; its ranking/industry/
            # winsorization/market-regime order is neither reimplemented nor edited.
            dataset._list_daily_tickers = lambda universe=universe: universe
            dataset.load_single_stock = lambda ticker, **kwargs: None if cache[ticker] is None else cache[ticker].copy()
            raw_tickers = [ticker for ticker,item in manifest['raw_metadata'].items() if branch == 'B' or item['branch'] == 'existing']
            try:
                with branch_daily_enumeration(raw_tickers):
                    market_regime.build_market_regime_features.cache_clear()
                    snapshot = dataset.build_latest_snapshot(verbose=False)
                    expected_regime = market_regime.add_market_regime_features(snapshot[['ticker','Date']])
                    actual = snapshot[['ticker','Date',*dataset.REGIME_FEATURE_COLS]].sort_values(['ticker','Date']).reset_index(drop=True)
                    expected = expected_regime[['ticker','Date',*dataset.REGIME_FEATURE_COLS]].sort_values(['ticker','Date']).reset_index(drop=True)
                    pd.testing.assert_frame_equal(actual,expected,check_exact=True)
                    market_regime.build_market_regime_features().to_csv(OUT/f'market_regime_{branch}.csv',index=False,encoding='utf-8-sig')
                    emit('branch_market_regime_verified', branch=branch, raw_source_tickers=len(raw_tickers), shared_cache_regime_columns=0, canonical_join_exact=True)
            finally:
                dataset._list_daily_tickers = original_list
                dataset.load_single_stock = original_loader
            snapshot.to_pickle(OUT/f'snapshot_{branch}.pkl')
            provider = predict.build_latest_snapshot
            predict.build_latest_snapshot = lambda **kwargs: snapshot.copy(deep=True)
            try:
                pred, meta = predict.predict_all(top_n=30, model_slot='production', save_snapshot=False)
            finally:
                predict.build_latest_snapshot = provider
            pred.to_csv(OUT/f'predictions_{branch}_{ASOF}.csv', index=False, encoding='utf-8-sig')
            top = predict.apply_sector_cap(pred, top_n=30)
            top.to_csv(OUT/f'top30_capped_{branch}.csv', index=False, encoding='utf-8-sig')
            frames[branch] = (pred, top, snapshot)
            emit('pinned_prediction_complete', branch=branch, snapshot_rows=len(snapshot), date_counts=snapshot.Date.astype(str).value_counts().to_dict(), recommendations=pred.recommendation.value_counts().to_dict(), top30_capped=len(top))
    conn.close()
    a, topa, snapa = frames['A']
    b, topb, snapb = frames['B']
    joined = a.merge(b, on=['ticker','date'], suffixes=('_A','_B'), how='inner', validate='one_to_one')
    assert len(joined) == len(a)
    stats = {}
    # leaderboard_score deliberately uses -inf outside the two-stage candidate
    # pool. Compare that pool and Top N membership, not meaningless inf-inf deltas.
    for col in ['prob_edge','pred_return_20d','up_prob','alpha_win_prob_20d','risk_adjusted_return']:
        if col+'_A' not in joined:
            continue
        delta = pd.to_numeric(joined[col+'_B']) - pd.to_numeric(joined[col+'_A'])
        joined[col+'_delta'] = delta
        stats[col] = {'mean_delta':float(delta.mean()), 'median_delta':float(delta.median()), 'min_delta':float(delta.min()), 'max_delta':float(delta.max()), 'max_abs_delta':float(delta.abs().max()), 'p95_abs_delta':float(delta.abs().quantile(.95)), 'p99_abs_delta':float(delta.abs().quantile(.99)), 'nonzero_rows':int(delta.ne(0).sum()), 'largest_absolute_changes':joined.loc[delta.abs().nlargest(15).index, ['ticker','date',col+'_A',col+'_B',col+'_delta']].to_dict('records')}
    joined.to_csv(OUT/'existing_ticker_prediction_deltas.csv', index=False, encoding='utf-8-sig')
    changes = joined[joined.recommendation_A != joined.recommendation_B]
    changes.to_csv(OUT/'recommendation_changes.csv', index=False, encoding='utf-8-sig')
    finite = {branch:{col:bool(np.isfinite(pd.to_numeric(pred[col])).all()) for col in ['prob_edge','pred_return_20d','up_prob','flat_prob','down_prob','alpha_win_prob_20d'] if col in pred} for branch,(pred,_,_) in frames.items()}
    # Inspect actual input coverage, including the legacy boolean-unknown behavior.
    conn = duckdb.connect(str(DB), read_only=True)
    new_coverage = []
    for ticker in manifest['new_tickers']:
        stock_raw = get_raw(conn, manifest['raw_metadata'], ticker)
        feat = cache.get(ticker)
        item = {'ticker':ticker,'eligible':feat is not None,'raw_missing_latest20':{col:int(stock_raw[col].tail(20).isna().sum()) for col in RAW_INSTITUTIONAL}}
        if feat is not None:
            item['missing_feature_columns'] = [c for c in feat.columns if feat[c].isna().all()]
            item['institutional_boolean_features'] = {c:float(feat[c].iloc[-1]) for c in ['foreign_trust_sync','foreign_reversal_buy','foreign_buy_streak','trust_reversal_buy','trust_buy_streak'] if c in feat}
        new_coverage.append(item)
    conn.close()
    verify_files(manifest['files'])
    assert digest(DB) == manifest['db_sha256']
    assert raw_fingerprints == {ticker:fingerprint(frame) for ticker,frame in cache.items() if frame is not None}, 'Canonical postprocessing mutated shared per-stock raw features'
    compare_columns = sorted(set(snapa.columns)&set(snapb.columns)-{'ticker','Date'})
    sx = snapa.merge(snapb,on=['ticker','Date'],suffixes=('_A','_B'),validate='one_to_one')
    feature_changes = []
    for col in compare_columns:
        equal = sx[col+'_A'].eq(sx[col+'_B']) | (sx[col+'_A'].isna() & sx[col+'_B'].isna())
        if not equal.all():
            feature_changes.append({'feature':col,'existing_changed_rows':int((~equal).sum())})
    report = {'as_of':ASOF,'completed':datetime.now().isoformat(),'seconds':round(time.monotonic()-started,2),'status':'RESEARCH_AB_COMPLETE_NOT_PROMOTION','old_eligible':len(a),'new_eligible':len(b)-len(a),'union_eligible':len(b),'one_to_one_existing_join':len(joined),'raw_features_computed_once':True,'all_raw_values_and_dtypes_verified':True,'input_hashes_unchanged':True,'db_sha256':manifest['db_sha256'],'finite_predictions':finite,'prediction_deltas':stats,'recommendation_changes':changes[['ticker','date','recommendation_A','recommendation_B']].to_dict('records'),'rank_top30':{branch:pred.head(30).ticker.tolist() for branch,(pred,_,_) in frames.items()},'capped_buy_top30':{'A':topa.ticker.tolist(),'B':topb.ticker.tolist()},'cross_sectional_feature_changes':feature_changes,'new_source_coverage':new_coverage,'limitations':['Source-universe sensitivity only; not a historical performance backtest.','Frozen canonical code retains existing boolean NaN comparisons / raw penalty-context fillna behavior; uncovered institutional inputs are explicitly reported, not treated as source-complete.','Latest eligible row per stock at or before 2026-09-04 follows canonical inference; date cohorts are reported in the stage log.','No production source promotion, model training, portfolio ledger updates, email, or deployment.']}
    write_json(OUT/'ab_report.json',report)
    emit('ab_complete', old_eligible=len(a), new_eligible=len(b)-len(a), changed_recommendations=len(changes), stats={c:{k:v for k,v in val.items() if k!='largest_absolute_changes'} for c,val in stats.items()}, finite_predictions=finite)


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve() != BASE or 'pipeline_acceptance_20260906' not in str(BASE):
        raise RuntimeError('Run only through the existing physical-workspace acceptance guard')
    OUT.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','run'])
    parser.add_argument('--source-dir',type=Path)
    parser.add_argument('--source-manifest',type=Path)
    args=parser.parse_args()
    warnings.filterwarnings('ignore',category=pd.errors.PerformanceWarning)
    warnings.filterwarnings('ignore',category=FutureWarning)
    if args.mode=='prepare':
        if args.source_dir is None or args.source_manifest is None:
            parser.error('prepare requires --source-dir and --source-manifest')
        prepare(args.source_dir,args.source_manifest)
    else:
        run()


if __name__=='__main__':
    main()
