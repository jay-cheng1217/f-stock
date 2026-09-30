"""One-off, fail-closed provenance bridge for the verified 2026-09-06 E snapshot.

Default is a read-only source audit plus a research receipt. --publish requires
the exact reviewed audit SHA and writes only the snapshot/lineage pair. It never
copies research predictions, trains, changes selection, or writes a ledger.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime
import glob
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
ISO = BASE/'output/pipeline_acceptance_20260906/workspace'
OUT = ISO/'ml/reports/research/source_universe_ab_20260906'
REPORT = BASE/'output/source_bridge_preflight_20260906/bridge_audit.json'
EXPECTED_SNAPSHOT = '66a3f1fe25da3d551b4b65df812e4eed80f138706607d361005cdccd881212a0'
EXPECTED_RAW = '3d98f6b14b9691f1228eb139060568a15bb316e22f32bc19a84123c68ab56d06'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(2**20), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def key(path):
    return str(Path(path)).replace('\\','/').lower()


def executable_ast(path):
    tree=ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    for node in ast.walk(tree):
        if isinstance(node,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
                node.body.pop(0)
    return ast.dump(tree,include_attributes=False)


def prediction_math(path):
    """Compare existing calculation functions, excluding only cache IO plumbing.

    run_prediction is an output wrapper whose new prediction certificate is
    independently reviewed by root; all functions it calls for numbers remain
    in this exact executable-AST comparison.
    """
    tree=ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    result={}
    for node in tree.body:
        if not isinstance(node,ast.FunctionDef) or node.name=='run_prediction':continue
        if node.name=='predict_all':
            node.body=[child for child in node.body if not (
                isinstance(child,ast.ImportFrom) and child.module=='ml.snapshot_lineage'
                or isinstance(child,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='snapshot_sources' for t in child.targets)
                or isinstance(child,ast.If) and isinstance(child.test,ast.Name) and child.test.id=='save_snapshot'
            )]
        result[node.name]=ast.dump(node,include_attributes=False)
    return result


def audit():
    from ml import dataset
    from ml.snapshot_lineage import source_state, assert_sources_unchanged
    from ml.features.registry import FEATURE_REGISTRY, get_available_features
    from ml.universe import reload_etf_universe,reload_retired_tickers
    issues=[]; evidence=[]
    def check(ok, reason):
        if not ok:issues.append(reason)
    def receipt(path):
        p=Path(path);evidence.append({'path':str(p),'sha256':digest(p)});return read(p)

    frozen=receipt(OUT/'verified_rerun/frozen_manifest.json')
    previous=receipt(OUT/'verified_rerun/rerun_equivalence.json')
    check(previous['rerun_input_hashes_unchanged'] and all(previous['all_original_completed_outputs_exact'].values()),'Verified rerun receipt is not exact')
    check(previous['original_run_remains_failed'],'Initial failed run must remain documented')
    old={key(Path(x['path']).relative_to(ISO)):x for x in frozen['files'] if Path(x['path']).is_relative_to(ISO)}
    current=source_state(include_hashes=True)
    actual={key(Path(x['path']).relative_to(BASE)):x for x in current['files']}
    expected={}; classifications={}
    def allow(relative, sha, category):
        expected[key(relative)]=sha;classifications[key(relative)]=category

    # Exact candidate bytes bind these zero-latest-feature proofs to production.
    margin1=receipt(BASE/'output/margin_20260320_repair_full_v2_20260906/validation.json')
    margin2=receipt(BASE/'output/margin_identifier_history_staged_20260906/validation.json')
    check(not margin1['feature_ab']['actual_latest_changed_tickers'],'March margin latest features changed')
    check(not margin2['latest_stock_universe_changes'] and margin2['non_margin_value_changes']==0,'Identifier margin latest ordinary-stock features changed')
    for name in ['margin_20260320_repair_full_v2_20260906','margin_identifier_history_staged_20260906']:
        plan=receipt(BASE/'output'/name/'promotion_plan.json')
        for item in plan['files']:
            check(digest(item['source'])==item['after_sha256'],f'Margin candidate changed: {item["source"]}')
            allow(item['target'],item['after_sha256'],'margin_source_latest_stock_feature_exact_zero')

    classic=receipt(OUT/'enriched_v3_classic/manifest.json')
    check(classic['companies']==84 and classic['ohlcv_changes']==0 and classic['ml_technical_changes']==0,'New84 classic equivalence failed')
    for item in classic['outputs']:
        check(digest(item['path'])==item['sha256'],f'New84 candidate changed: {item["ticker"]}')
        allow('日K資料/'+item['ticker']+'.csv',item['sha256'],'B_new84_v3_exact_candidate')
    c=receipt(OUT/'quarter_C/frozen_q2_manifest.json')
    check(receipt(OUT/'quarter_C/q2_C_report.json')['status']=='RESEARCH_Q2_C_COMPLETE_NOT_PROMOTION','C incomplete')
    for item in c['sources']:
        if item['relative_path'].startswith('季報財務/'):
            allow(item['relative_path'],item['candidate_sha256'],'C_Q2_exact_candidate')
    e=receipt(OUT/'history_E/history_E_report.json')
    check(e['baseline_BS_cells_exact']==8784 and e['unaffected_cache_exact'],'E baseline proof failed')
    f=receipt(OUT/'history_E/retained_F_eligibility_equivalence.json')
    check(f['status']=='F_MODEL_INPUT_EXACT_ZERO' and f['canonical_BS_cells_compared']==8784 and not f['changed_cells'],'F not exact zero')
    f_manifest=receipt(BASE/'output/bs_retained_latest_F_20260906/manifest.json')
    check(digest(BASE/'output/bs_retained_latest_F_20260906/manifest.json')==f['source_manifest_sha256'],'F source proof changed')
    for item in f_manifest['sources']:
        check(digest(item['path'])==item['sha256'],f'F study source changed: {item["path"]}')
    for path in (BASE/'output/bs_retained_latest_F_20260906/after/資產負債').glob('*.csv'):
        allow('資產負債/'+path.name,digest(path),'E_history_plus_F_eligible_exact_zero')
    d=receipt(OUT/'history_E/tdcc_D_final_equivalence.json')
    check(d['status']=='D_FINAL_ELIGIBLE_INPUT_EXACT_ZERO' and d['baseline_cells_exact']==14274 and not d['changed_cells'],'D not exact zero')
    check(d['E_raw_cache_sha256']==EXPECTED_RAW,'D did not verify this E raw cache')
    allow('集保分散/tdcc_summary.csv',d['after_sha256'],'D_1098x13_latest_exact_zero')
    g=receipt(BASE/'output/valuation_fingerprint_family_20260906/feature_ab_v2/manifest.json')
    check(g['compared_cells']==10155 and g['changed_cells']==0 and g['raw_daily_db_sha256']==frozen['db_sha256'],'G wrong raw frame or nonzero')
    plan=receipt(BASE/'output/valuation_fingerprint_family_20260906/promotion_plan.json')
    for item in plan['files']:
        check(digest(item['source'])==item['after_sha256'],f'G candidate changed: {item["source"]}')
        allow(item['target'],item['after_sha256'],'G_2031x5_latest_exact_zero')
    v=receipt(BASE/'ml/reports/research/data_layer_consistency_20260906/valuation_20210510_repair.json')
    check(v['latest_feature_ab']['changed_cells']==0,'20210510 valuation latest proof failed')
    allow('估值資料/valuation_20210510.csv',v['candidate_sha256'],'valuation_20210510_latest_exact_zero')

    # The canonical reader uses only the latest foreign file's Issued_Shares.
    latest=[sorted((root/'外資持股').glob('*.csv'))[-1] for root in [ISO,BASE]]
    l,r=[pd.read_csv(p,dtype={'Ticker':str})[['Ticker','Issued_Shares']].sort_values('Ticker').reset_index(drop=True) for p in latest]
    check(latest[0].name==latest[1].name and l.equals(r),'Latest issued-share dependency changed')
    foreign_proof={'files':[{'path':str(p),'sha256':digest(p)} for p in latest], 'rows':len(l),'exact':l.equals(r),'consumer':'ml.dataset._load_issued_shares takes only sorted(files)[-1], Ticker and Issued_Shares; foreign history is not a snapshot input'}
    for rel,item in actual.items():
        if rel.startswith('外資持股/'):
            allow(rel,item['sha256'],'foreign_history_nonconsumed_latest_issued_shares_exact')

    check(executable_ast(ISO/'ml/features/tdcc.py')==executable_ast(BASE/'ml/features/tdcc.py'),'TDCC executable code changed')
    allow('ml/features/tdcc.py',digest(BASE/'ml/features/tdcc.py'),'TDCC_docstring_only_AST_exact')
    allow('ml/snapshot_lineage.py',digest(BASE/'ml/snapshot_lineage.py'),'new_cache_certificate_helper_not_feature_math')
    # This file was omitted by the early physical-copy broad manifest. Re-evaluate
    # the actual complete current universe against the frozen branch manifest.
    check(digest(BASE/'config/retired_tickers.csv')==digest(ISO/'config/retired_tickers.csv'),'Retired metadata differs from frozen physical copy')
    reload_etf_universe();reload_retired_tickers()
    check(sorted(dataset._list_daily_tickers())==sorted(frozen['union_universe']),'Current canonical universe differs from verified B/E')
    allow('config/retired_tickers.csv',digest(BASE/'config/retired_tickers.csv'),'initial_manifest_omission_disclosed_full_universe_recomputed_exact')
    previous_groups=sorted(name for name,definition in FEATURE_REGISTRY.items() if all(any(Path(str(p).replace(str(BASE),str(ISO))).glob('*.csv')) for p in definition['requires']))
    check(previous_groups==sorted(get_available_features()),'Available feature group set differs')

    matrix=[]
    for rel,item in actual.items():
        baseline=old.get(rel)
        if baseline:
            check(Path(baseline['path']).is_file() and digest(baseline['path'])==baseline['sha256'],f'Frozen baseline changed: {rel}')
        if baseline and baseline['sha256']==item['sha256']:
            category='frozen_source_byte_exact'
        elif rel in expected and item['sha256']==expected[rel]:
            category=classifications[rel]
        else:
            category='UNPROVEN';issues.append('Unproved current source: '+rel)
        matrix.append({'relative_path':rel,'sha256':item['sha256'],'before_sha256':baseline['sha256'] if baseline else None,'category':category})
    # Full file-set changes (including deletion) cannot hide behind surviving files.
    iso_paths={key(Path(p).relative_to(ISO)) for pattern in current['patterns'] for p in glob.glob(pattern.replace(str(BASE).lower(),str(ISO))) if Path(p).is_file()}
    missing=sorted(iso_paths-set(actual))
    check(not missing,'Deleted canonical source files: '+str(missing))

    # Verify non-feature model inputs too. New, unselected training outputs are
    # intentionally irrelevant; fixed pins and selection files must be unchanged.
    pinned=[];code=[]
    for rel,item in old.items():
        if (rel.startswith('ml/models/') and (Path(rel).name.startswith('lgbm') or Path(rel).name in ['model_selection.json','registry.json'])) or rel in ['config/champion_pin_manifest.yaml']:
            target=BASE/rel
            check(target.is_file() and digest(target)==item['sha256'],'Model artifact/selection changed: '+rel)
            pinned.append({'relative_path':rel,'sha256':item['sha256']})
        if rel.endswith('.py') and rel.startswith(('ml/','backend/features/')) and rel not in ['ml/predict.py','ml/features/tdcc.py']:
            check(digest(BASE/rel)==item['sha256'],'Other ML code changed: '+rel)
            code.append({'relative_path':rel,'sha256':item['sha256']})
    contract=receipt(BASE/'output/pipeline_acceptance_20260906/production_model_contract.json')
    for item in contract['validation']['details']:
        for kind in ['model','meta']:
            check(digest(item[kind+'_path'])==item[kind+'_sha256'],'Contract pin changed: '+item[kind+'_path'])
    for rel in ['scripts/generate_entry_candidates.py','scripts/sector_strength.py','ml/disposition_active.csv']:
        if (ISO/rel).exists():check(digest(BASE/rel)==digest(ISO/rel),'Non-feature consumer source changed: '+rel)
    before_math,after_math=prediction_math(ISO/'ml/predict.py'),prediction_math(BASE/'ml/predict.py')
    check(all(after_math.get(name)==value for name,value in before_math.items()),'Predict numerical calculation function changed')
    sector_path=OUT/'canonical_sources_B/sector_strength.json'
    check(digest(BASE/'ml/data/sector_strength.json')==digest(sector_path),'Formal sector ranking is not verified B/C/E')
    snapshot_path=OUT/'history_E/snapshot_E.pkl'
    check(digest(snapshot_path)==EXPECTED_SNAPSHOT,'Final E snapshot bytes changed')
    check(digest(OUT/'history_E/raw_feature_rows_E.pkl')==EXPECTED_RAW,'Final E raw cache bytes changed')
    snapshot=pd.read_pickle(snapshot_path)
    check(len(snapshot)==1098,'Final E row count wrong')
    assert_sources_unchanged(current)
    report={'status':'READY_FOR_REVIEWED_SNAPSHOT_PUBLICATION' if not issues else 'BLOCKED_UNPROVEN_DEPENDENCY','created_at':datetime.now().isoformat(),'issues':issues,'snapshot':{'path':str(snapshot_path),'sha256':EXPECTED_SNAPSHOT,'raw_cache_sha256':EXPECTED_RAW,'rows':len(snapshot)},'source_state':current,'source_matrix':matrix,'category_counts':dict(Counter(x['category'] for x in matrix)),'missing_files':missing,'evidence':evidence,'fixed_artifacts':pinned,'unchanged_ML_code':code,'foreign_dependency':foreign_proof,'sector_strength_sha256':digest(sector_path),'available_groups':previous_groups,'initial_manifest_deviation':'Initial run FAIL retained; v2 rerun exact. retired_tickers.csv omitted from initial broad manifest, now physical bytes and complete canonical universe separately proved. New cache-lineage helper only certifies proven E; not a retroactive certificate for arbitrary old pickle.','publication_scope':'Only E snapshot and lineage metadata. Root must execute current pinned ml.predict.run_prediction with verified-load provider and save_snapshot=False, then downstream producers. No research prediction CSV adoption.','limitations':['Source sensitivity is not model calibration or realized performance validation.','Q2 official knowledge date 2026-09-06; actual per-ticker price dates retained, observation date 2026-09-07.','Large A/B/C probability and recommendation changes remain material even though capped Top1 is unchanged.','No training, model selection, thresholds, capital allocation, ledger writes, email or service restart.']}
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'issues':issues,'categories':report['category_counts'],'report':str(REPORT),'report_sha256':digest(REPORT)},ensure_ascii=False),flush=True)
    return report


def publish(expected_report_sha):
    from ml.snapshot_lineage import source_state,save_snapshot
    from ml.predict import SNAPSHOT_CACHE_PATH
    if not expected_report_sha or digest(REPORT)!=expected_report_sha:
        raise RuntimeError('Exact reviewed audit report SHA is required')
    report=read(REPORT)
    if report['status']!='READY_FOR_REVIEWED_SNAPSHOT_PUBLICATION' or report['issues']:
        raise RuntimeError('Source bridge has unresolved dependencies')
    current=source_state(include_hashes=True)
    if current!=report['source_state']:
        raise RuntimeError('Formal sources changed after reviewed audit')
    for item in report['evidence']:
        if digest(item['path'])!=item['sha256']:raise RuntimeError('Bridge proof changed: '+item['path'])
    source=Path(report['snapshot']['path'])
    if digest(source)!=EXPECTED_SNAPSHOT:raise RuntimeError('Verified E snapshot changed')
    metadata={'source_bridge':{'audit_path':str(REPORT),'audit_sha256':expected_report_sha,'research_snapshot_path':str(source),'research_snapshot_sha256':EXPECTED_SNAPSHOT,'categories':report['category_counts'],'evidence':report['evidence']},'knowledge_time':'2026-09-06','observation_date':'2026-09-07'}
    result=save_snapshot(pd.read_pickle(source),SNAPSHOT_CACHE_PATH,current,metadata=metadata)
    path=REPORT.parent/'snapshot_publication.json'
    path.write_text(json.dumps({'status':'SNAPSHOT_PUBLISHED_PREDICTION_NOT_RUN','path':SNAPSHOT_CACHE_PATH,'sha256':result['snapshot_sha256'],'audit_sha256':expected_report_sha,'time':datetime.now().isoformat()},ensure_ascii=False,indent=2),encoding='utf-8')
    print(path.read_text(encoding='utf-8'),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--expected-report-sha')
    args=parser.parse_args()
    if args.publish:publish(args.expected_report_sha)
    else:
        result=audit()
        if result['issues']:raise SystemExit(1)
