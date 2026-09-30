"""Read-only fixed-frame eligibility for the retained-source F comparison."""
import json
import os
from pathlib import Path
import duckdb
from scripts import source_universe_snapshot_ab as ab


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Physical acceptance workspace only')
    from ml import dataset,universe
    manifest=json.loads((ab.OUT/'verified_rerun/frozen_manifest.json').read_text(encoding='utf-8'))
    conn=duckdb.connect(str(ab.DB),read_only=True)
    with ab.duckdb_daily_reader(conn,manifest['raw_metadata']):
        results={ticker:{'retired_gate':universe.is_retired_ticker(ticker),'in_union':ticker in manifest['union_universe'],'diagnostic':dataset.diagnose_stock_eligibility(ticker)} for ticker in ['2867','5371','3454','4130']}
    conn.close()
    report={'source':'same union DuckDB as A/B/C/E','db_sha256':ab.digest(ab.DB),'results':results,'retired_config_sha256':ab.digest(universe.RETIRED_TICKERS_PATH),'dataset_sha256':ab.digest(Path(dataset.__file__))}
    ab.write_json(ab.OUT/'retained_eligibility.json',report)
    print(json.dumps(results,ensure_ascii=False,default=str))


if __name__=='__main__':
    main()
