"""Preserve the rejected run, audit two synchronized files, and rerun inference."""
from pathlib import Path
from datetime import datetime
import ast
import json
import os
import pickle
import shutil
import sys

from scripts import source_universe_snapshot_ab as ab


def executable_ast(data):
    tree=ast.parse(data.decode('utf-8-sig'))
    for node in ast.walk(tree):
        if isinstance(node,(ast.Module,ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
            node.body=node.body[1:]
    return ast.dump(tree,include_attributes=False)


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Physical acceptance workspace only')
    original_out=ab.OUT
    evidence=original_out/'input_deviation'
    out=original_out/'verified_rerun'
    out.mkdir(exist_ok=False)
    manifest=json.loads((original_out/'frozen_manifest.json').read_text(encoding='utf-8'))
    replacements={ab.BASE/'ml/features/tdcc.py':evidence/'tdcc_recovered.py',ab.BASE/'scripts/backfill_valuation.py':evidence/'backfill_valuation_git_b74d7cc9.py'}
    original_items={Path(row['path']):row for row in manifest['files']}
    ab.verify_files([item for item in manifest['files'] if Path(item['path']) not in replacements])
    assert ab.digest(ab.DB)==manifest['db_sha256']
    assert executable_ast((evidence/'tdcc_synced.py').read_bytes())==executable_ast((evidence/'tdcc_recovered.py').read_bytes())
    deviations=[]
    for path,replacement in replacements.items():
        deviations.append({'path':str(path),'frozen_sha256':original_items[path]['sha256'],'synchronized_sha256':ab.digest(path),'replacement_sha256':ab.digest(replacement),'exact_frozen_restoration':ab.digest(replacement)==original_items[path]['sha256']})
        shutil.copyfile(replacement,path)
    import ml.dataset as dataset
    import ml.predict as predict
    assert 'scripts.backfill_valuation' not in sys.modules
    local_modules={name:str(Path(module.__file__).resolve()) for name,module in list(sys.modules.items()) if getattr(module,'__file__',None) and Path(module.__file__).resolve().is_relative_to(ab.BASE)}
    assert not any(Path(path).name=='backfill_valuation.py' for path in local_modules.values())
    cache=pickle.loads((original_out/'raw_feature_rows.pkl').read_bytes())
    fingerprints=json.loads((original_out/'raw_feature_fingerprints.json').read_text(encoding='utf-8'))
    assert fingerprints=={ticker:ab.fingerprint(frame) for ticker,frame in cache.items() if frame is not None}
    report={'observed_at':datetime.now().isoformat(),'original_execution':'FAIL_FROZEN_INPUT_CHECK','original_receipt':str(ab.BASE.parent/'source_union_ab_inference.json'),'reason':'Another validation synchronized two source files during the first run; original receipt and outputs are retained.','files':deviations,'tdcc_executable_ast_equal':True,'backfill_valuation_imported':False,'local_import_closure':local_modules,'cache_fingerprints_unchanged':True,'raw_cache_reuse_basis':['Only TDCC documentation changed; executable AST is identical.','Valuation HTTP backfill is not imported or called by dataset/predict; valuation CSV sources all match the original frozen hashes.','All other frozen inputs and union DuckDB hash match; cached per-stock rows match their saved fingerprints.'],'waiver':'Only backfill_valuation.py bytes cannot be reconstructed after mixed newline normalization; non-executed producer is restored to b74d7cc9 code and explicitly re-frozen in v2, not called exact original restoration.'}
    ab.write_json(evidence/'input_deviation.json',report)
    for item in manifest['files']:
        if Path(item['path']) in replacements:
            item['sha256']=ab.digest(item['path'])
            item['bytes']=Path(item['path']).stat().st_size
    manifest['version']=2
    manifest['input_deviation']=str(evidence/'input_deviation.json')
    manifest['first_manifest_sha256']=ab.digest(original_out/'frozen_manifest.json')
    manifest['files'].append({'path':str(Path(__file__).resolve()),'sha256':ab.digest(__file__),'bytes':Path(__file__).stat().st_size})
    ab.write_json(out/'frozen_manifest.json',manifest)
    shutil.copyfile(original_out/'raw_feature_rows.pkl',out/'raw_feature_rows.pkl')
    ab.OUT=out
    ab.run()
    assert 'scripts.backfill_valuation' not in sys.modules
    import pandas as pd
    equality={}
    for branch in ['A','B']:
        for filename in [f'snapshot_{branch}.pkl',f'predictions_{branch}_{ab.ASOF}.csv']:
            read=pd.read_pickle if filename.endswith('.pkl') else pd.read_csv
            pd.testing.assert_frame_equal(read(original_out/filename),read(out/filename),check_exact=True)
            equality[filename]=True
    ab.write_json(out/'rerun_equivalence.json',{'all_original_completed_outputs_exact':equality,'input_deviation_receipt':str(evidence/'input_deviation.json'),'original_run_remains_failed':True,'rerun_input_hashes_unchanged':True})
    ab.emit('verified_rerun_complete',exact_outputs=equality)


if __name__=='__main__':
    main()
