import ast
import hashlib
import json
import logging
import os
import re
import glob
from pathlib import Path
import types

import pandas as pd
import pytest

from ml import snapshot_lineage as lineage


@pytest.fixture
def sources(tmp_path, monkeypatch):
    daily=tmp_path/'daily'
    daily.mkdir()
    raw=daily/'2330.csv'
    raw.write_text('Date,Close\n2026-09-04,100\n',encoding='utf-8')
    auxiliary=tmp_path/'financial_2026Q2.csv'
    auxiliary.write_text('Ticker,EPS\n2330,10\n',encoding='utf-8')
    monkeypatch.setattr(lineage,'canonical_source_patterns',lambda:[str(daily/'*.csv'),str(auxiliary)])
    return raw,auxiliary,tmp_path/'snapshot.pkl'


def publish(sources):
    snapshot=pd.DataFrame({'ticker':['2330'],'Date':['2026-09-04'],'Close':[100.]})
    lineage.save_snapshot(snapshot,sources[2],lineage.source_state(include_hashes=True))
    return snapshot


def replace_same_date(path,old,new):
    stat=path.stat()
    path.write_text(path.read_text().replace(old,new))
    os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))


@pytest.mark.parametrize('source_index',[0,1])
def test_same_date_same_size_source_correction_rejects_snapshot(sources,source_index):
    publish(sources)
    before=lineage.source_state()['signature']
    path=sources[source_index]
    replace_same_date(path,'100' if source_index==0 else '10','101' if source_index==0 else '11')
    assert lineage.source_state()['signature']!=before
    assert lineage.load_snapshot(sources[2]) is None


@pytest.mark.parametrize('change',['add','delete'])
def test_file_set_change_rejects_snapshot(sources,change):
    publish(sources)
    if change=='add':
        sources[0].with_name('7780.csv').write_bytes(sources[0].read_bytes())
    else:
        sources[0].unlink()
    assert lineage.load_snapshot(sources[2]) is None


def test_valid_reuse_hashes_snapshot_but_not_all_source_contents(sources,monkeypatch):
    expected=publish(sources)
    monkeypatch.setattr(lineage,'_file_hash',lambda path:pytest.fail('Reuse must not hash GB of raw sources'))
    pd.testing.assert_frame_equal(lineage.load_snapshot(sources[2]),expected)


def test_feature_availability_change_rejects_even_when_source_pattern_files_match(sources, monkeypatch):
    from ml.features import registry
    monkeypatch.setattr(registry, 'get_available_features', lambda: {'technical': {}})
    publish(sources)
    monkeypatch.setattr(registry, 'get_available_features', lambda: {'technical': {}, 'tdcc': {}})
    assert lineage.load_snapshot(sources[2]) is None


@pytest.mark.parametrize('damage',['legacy','pickle','manifest','invalid_pickle_with_matching_hash','missing_hash_evidence'])
def test_legacy_or_corrupt_pairs_fail_closed(sources,damage):
    publish(sources)
    meta=Path(str(sources[2])+'.manifest.json')
    if damage=='legacy':
        meta.unlink()
    elif damage=='manifest':
        meta.write_text('{')
    elif damage=='missing_hash_evidence':
        data=json.loads(meta.read_text())
        data['sources']['content_signature']=None
        meta.write_text(json.dumps(data))
    else:
        sources[2].write_bytes(b'not a pickle')
        if damage=='invalid_pickle_with_matching_hash':
            data=json.loads(meta.read_text())
            data['snapshot_sha256']=hashlib.sha256(sources[2].read_bytes()).hexdigest()
            meta.write_text(json.dumps(data))
    assert lineage.load_snapshot(sources[2]) is None


def test_changed_source_during_build_does_not_certify_snapshot(sources):
    expected=publish(sources)
    original=sources[2].read_bytes()
    before=lineage.source_state(include_hashes=True)
    replace_same_date(sources[0],'100','101')
    with pytest.raises(RuntimeError,match='changed during'):
        lineage.save_snapshot(expected,sources[2],before)
    assert sources[2].read_bytes()==original


def test_change_while_hashing_is_rejected(sources,monkeypatch):
    original=lineage._file_hash
    def racing(path):
        result=original(path)
        if Path(path)==sources[0]:
            replace_same_date(Path(path),'100','101')
        return result
    monkeypatch.setattr(lineage,'_file_hash',racing)
    with pytest.raises(RuntimeError,match='while hashing'):
        lineage.source_state(include_hashes=True)


def app_function(name):
    """Exercise the real app adapter without starting its server/DB lifespan."""
    path=Path(__file__).resolve().parents[1]/'app.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    node=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name==name)
    namespace={'os':os,'pipeline_log':logging.getLogger('snapshot-test')}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
    return namespace[name]


def test_app_pipeline_adapter_requires_current_lineage(sources,monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'ml.predict',types.SimpleNamespace(SNAPSHOT_CACHE_PATH=str(sources[2])))
    load=app_function('_load_pipeline_snapshot')
    expected=publish(sources)
    pd.testing.assert_frame_equal(load(),expected)
    replace_same_date(sources[0],'100','101')
    assert load() is None


def test_app_memory_context_changes_when_same_date_source_changes(sources,monkeypatch):
    from ml import model_selection,config
    meta=sources[2].with_name('model_meta.json')
    meta.write_text(json.dumps({'model_file':str(sources[2].with_name('model.txt'))}))
    monkeypatch.setattr(model_selection,'resolve_base_meta_path',lambda slot:str(meta))
    monkeypatch.setattr(config,'MODEL_DIR',str(sources[2].parent))
    function=app_function('_prediction_context_key')
    function.__globals__.update({'json':json,'glob':glob,'__file__':str(Path(__file__).resolve().parents[1]/'app.py'),'PRODUCTION_PREDICTION_RE':re.compile(r'^predictions_.*\.csv$'),'_artifact_fingerprint':lambda path:None,'_glob_artifact_fingerprints':lambda path:[]})
    first=function()
    replace_same_date(sources[0],'100','101')
    assert first!=function()


def test_predict_save_boundary_rejects_sources_updated_during_build(sources,monkeypatch):
    path=Path(__file__).resolve().parents[1]/'ml/predict.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='predict_all')
    # Exercise the actual production construction/publication prefix; omit the
    # independent model math after snapshot_zs, so no protected model is loaded.
    end=next(i for i,node in enumerate(function.body) if isinstance(node,ast.AnnAssign) or (isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='snapshot_zs' for target in node.targets)))
    function.body=function.body[:end]
    snapshot=pd.DataFrame({'ticker':['2330'],'Date':['2026-09-04']})
    def build(**kwargs):
        replace_same_date(sources[0],'100','101')
        return snapshot
    namespace={'load_selected_model':lambda **kwargs:(None,{'feature_columns':[]}), 'load_v2_model':lambda:(None,None),'load_v2_alpha_classifier':lambda:(None,None),'load_two_stage_ranker':lambda:(None,None),'build_latest_snapshot':build,'SNAPSHOT_CACHE_PATH':str(sources[2])}
    monkeypatch.setattr(lineage,'clear_source_memo_caches',lambda:None)
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(path),'exec'),namespace)
    with pytest.raises(RuntimeError,match='changed during'):
        namespace['predict_all']()
    assert not sources[2].exists()


def test_source_memo_caches_are_cleared_before_long_lived_rebuild():
    from ml.features import fundamental,eps,balance_sheet,tdcc
    from ml import dataset
    for cache in [fundamental._FINANCIAL_CACHE,eps._EPS_CACHE,balance_sheet._BS_CACHE,tdcc._TDCC_CACHE]:
        cache['test-stale']=object()
    dataset._ISSUED_SHARES_CACHE={'2330':1.}
    lineage.clear_source_memo_caches()
    assert not fundamental._FINANCIAL_CACHE and not eps._EPS_CACHE
    assert not balance_sheet._BS_CACHE and not tdcc._TDCC_CACHE
    assert dataset._ISSUED_SHARES_CACHE is None


def test_canonical_dependency_inventory_includes_factor_and_universe_inputs():
    patterns=lineage.canonical_source_patterns()
    for name in ['corporate_action_price_factors.csv','ex_dividend_calendar.csv','etf_universe.csv','retired_tickers.csv','sector_mapping.csv','tdcc_summary.csv','bs_*.csv']:
        assert any(path.endswith(name) for path in patterns),name
