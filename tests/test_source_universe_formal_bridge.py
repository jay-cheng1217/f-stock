import json
import sys
import types

import pytest

from scripts import source_universe_formal_bridge as bridge


def test_prediction_math_keeps_numeric_changes_visible(tmp_path):
    old=tmp_path/'old.py';new=tmp_path/'new.py'
    old.write_text('def predict_all(save_snapshot=True):\n    snapshot=build_latest_snapshot()\n    if save_snapshot:\n        snapshot.to_pickle(path)\n    return snapshot*2\n',encoding='utf-8')
    new.write_text('def predict_all(save_snapshot=True):\n    from ml.snapshot_lineage import source_state\n    snapshot_sources=source_state()\n    snapshot=build_latest_snapshot()\n    if save_snapshot:\n        save_verified_snapshot(snapshot,path,snapshot_sources)\n    return snapshot*2\n',encoding='utf-8')
    assert bridge.prediction_math(old)==bridge.prediction_math(new)
    new.write_text(new.read_text(encoding='utf-8').replace('snapshot*2','snapshot*3'),encoding='utf-8')
    assert bridge.prediction_math(old)!=bridge.prediction_math(new)


@pytest.mark.parametrize('mode',['changed_source','blocked_report','snapshot_corrupt','wrong_review_sha'])
def test_publish_refuses_without_matching_review_and_sources(tmp_path,monkeypatch,mode):
    saved=[]
    state={'signature':'reviewed','content_signature':'fullhash'}
    current=state if mode!='changed_source' else {**state,'signature':'changed'}
    fake=types.SimpleNamespace(source_state=lambda **kw:current,save_snapshot=lambda *a,**kw:saved.append(True))
    monkeypatch.setitem(sys.modules,'ml.snapshot_lineage',fake)
    monkeypatch.setitem(sys.modules,'ml.predict',types.SimpleNamespace(SNAPSHOT_CACHE_PATH=str(tmp_path/'production.pkl')))
    snapshot=tmp_path/'snapshot.pkl';snapshot.write_bytes(b'corrupt')
    report={'status':'BLOCKED_UNPROVEN_DEPENDENCY' if mode=='blocked_report' else 'READY_FOR_REVIEWED_SNAPSHOT_PUBLICATION','issues':[],'source_state':state,'evidence':[],'snapshot':{'path':str(snapshot)}}
    path=tmp_path/'review.json';path.write_text(json.dumps(report),encoding='utf-8')
    monkeypatch.setattr(bridge,'REPORT',path)
    sha='wrong' if mode=='wrong_review_sha' else bridge.digest(path)
    with pytest.raises(RuntimeError):bridge.publish(sha)
    assert not saved
    assert not (tmp_path/'production.pkl').exists()
