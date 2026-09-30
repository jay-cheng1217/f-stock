import json

import pytest

from scripts.verified_source_promotion import promote, sha


def plan_fixture(tmp_path):
    candidate = tmp_path / 'output/stage/a.csv'
    candidate.parent.mkdir(parents=True)
    candidate.write_text('verified data', encoding='utf-8')
    target = tmp_path / '日K資料/2330.csv'
    target.parent.mkdir()
    target.write_text('old data', encoding='utf-8')
    evidence = tmp_path / 'evidence.json'
    evidence.write_text('source comparison passed', encoding='utf-8')
    plan = {'label': 'fixture', 'evidence': [{'path': str(evidence), 'sha256': sha(evidence)}],
            'files': [{'source': str(candidate), 'target': str(target.relative_to(tmp_path)),
                       'before_sha256': sha(target), 'after_sha256': sha(candidate)}]}
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan), encoding='utf-8')
    return path, plan, target


def test_publish_preserves_original_and_is_resumable(tmp_path):
    path, plan, target = plan_fixture(tmp_path)
    assert promote(path, root=tmp_path)['status'] == 'PREFLIGHT_PASS'
    assert target.read_text() == 'old data'
    result = promote(path, root=tmp_path, publish=True)
    assert result['status'] == 'PUBLISHED'
    backup = tmp_path / 'output/data_layer_consistency_20260906/source_promotion/fixture/日K資料/2330.csv'
    assert backup.read_text() == 'old data'
    assert target.read_text() == 'verified data'
    assert promote(path, root=tmp_path, publish=True)['files'][0]['status'] == 'already_published'


@pytest.mark.parametrize('directory', ['季報財務', '資產負債'])
def test_quarterly_source_promotion_keeps_original_backup(tmp_path, directory):
    path, plan, previous_target = plan_fixture(tmp_path)
    target = tmp_path / directory / 'financial_2026Q2.csv'
    target.parent.mkdir()
    target.write_bytes(previous_target.read_bytes())
    plan['files'][0]['target'] = str(target.relative_to(tmp_path))
    path.write_text(json.dumps(plan), encoding='utf-8')
    receipt = promote(path, root=tmp_path, publish=True)
    backup = tmp_path / 'output/data_layer_consistency_20260906/source_promotion/fixture' / target.relative_to(tmp_path)
    assert receipt['status'] == 'PUBLISHED'
    assert backup.read_text() == 'old data'
    assert target.read_text() == 'verified data'


@pytest.mark.parametrize('problem', ['model', 'escape', 'duplicate', 'changed_source', 'changed_review', 'concurrent'])
def test_preflight_failure_never_replaces_target(tmp_path, problem):
    path, plan, target = plan_fixture(tmp_path)
    if problem == 'model':
        plan['files'][0]['target'] = 'ml/models/lgbm.txt'
    elif problem == 'escape':
        plan['files'][0]['target'] = '../outside.csv'
    elif problem == 'duplicate':
        plan['files'].append(plan['files'][0].copy())
    elif problem == 'changed_source':
        plan['files'][0]['after_sha256'] = 'mismatch'
    elif problem == 'changed_review':
        plan['evidence'][0]['sha256'] = 'mismatch'
    else:
        plan['files'][0]['before_sha256'] = 'concurrent'
    path.write_text(json.dumps(plan), encoding='utf-8')
    with pytest.raises(ValueError):
        promote(path, root=tmp_path, publish=True)
    assert target.read_text() == 'old data'
