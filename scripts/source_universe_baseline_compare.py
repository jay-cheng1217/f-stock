"""Keep existing production drift separate from the source-universe A/B effect."""
from datetime import datetime
import json
import os
from pathlib import Path
import numpy as np
import pandas as pd
from scripts import source_universe_snapshot_ab as ab


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Run through the acceptance guard')
    manifest=json.loads((ab.OUT/'formal_baseline_manifest.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        assert ab.digest(item['source'])==item['sha256']==ab.digest(item['copy'])
    production=pd.read_csv(ab.OUT/f'formal_baseline_predictions_{ab.ASOF}.csv',dtype={'ticker':str})
    a=pd.read_csv(ab.OUT/f'predictions_A_{ab.ASOF}.csv',dtype={'ticker':str})
    joined=production.merge(a,on=['ticker','date'],suffixes=('_formal','_A'),how='inner',validate='one_to_one')
    stats={}
    for col in ['prob_edge','pred_return_20d','up_prob','flat_prob','down_prob','alpha_win_prob_20d','risk_adjusted_return']:
        if col+'_formal' not in joined or col+'_A' not in joined:
            continue
        left=pd.to_numeric(joined[col+'_formal'])
        right=pd.to_numeric(joined[col+'_A'])
        delta=right-left
        joined[col+'_delta']=delta
        stats[col]={'max_abs_delta':float(delta.abs().max()),'mean_delta':float(delta.mean()),'min_delta':float(delta.min()),'max_delta':float(delta.max()),'p95_abs_delta':float(delta.abs().quantile(.95)),'nonzero_rows':int(delta.ne(0).sum()),'gt_1e_7_rows':int(delta.abs().gt(1e-7).sum()),'largest':joined.loc[delta.abs().nlargest(10).index,['ticker','date',col+'_formal',col+'_A',col+'_delta']].to_dict('records')}
    changes=joined[joined.recommendation_formal!=joined.recommendation_A]
    joined.to_csv(ab.OUT/'formal_vs_A_prediction_deltas.csv',index=False,encoding='utf-8-sig')
    formal_snapshot=pd.read_pickle(ab.OUT/'formal_baseline_snapshot_cache.pkl')
    if isinstance(formal_snapshot,dict):
        formal_snapshot=formal_snapshot['df']
    asnapshot=pd.read_pickle(ab.OUT/'snapshot_A.pkl')
    shared=formal_snapshot.merge(asnapshot,on=['ticker','Date'],suffixes=('_formal','_A'),validate='one_to_one')
    feature_changes=[]
    for col in sorted(set(formal_snapshot.columns)&set(asnapshot.columns)-{'ticker','Date'}):
        left=shared[col+'_formal']
        right=shared[col+'_A']
        equal=left.eq(right)|(left.isna()&right.isna())
        if not equal.all():
            item={'column':col,'changed_rows':int((~equal).sum())}
            if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
                delta=pd.to_numeric(right)-pd.to_numeric(left)
                finite=delta[np.isfinite(delta)]
                item['max_abs_finite_delta']=float(finite.abs().max()) if len(finite) else None
                item['missingness_changes']=int(left.isna().ne(right.isna()).sum())
            feature_changes.append(item)
    model_cols=['base_model_file','two_stage_model_file','alpha_classifier_model_file','model_slot']
    report={'observed_at':datetime.now().isoformat(),'formal_csv_sha256':manifest['files'][0]['sha256'],'formal_snapshot_sha256':manifest['files'][1]['sha256'],'rows':{'formal':len(production),'A':len(a),'joined':len(joined)},'only_formal_tickers':sorted(set(production.ticker)-set(a.ticker)),'only_A_tickers':sorted(set(a.ticker)-set(production.ticker)),'prediction_deltas':stats,'recommendation_changes':changes[['ticker','date','recommendation_formal','recommendation_A']].to_dict('records'),'rank_top30':{'formal':production.head(30).ticker.tolist(),'A':a.head(30).ticker.tolist()},'model_metadata':{label:{col:frame[col].dropna().unique().tolist() for col in model_cols if col in frame} for label,frame in [('formal',production),('A',a)]},'snapshot_rows':{'formal':len(formal_snapshot),'A':len(asnapshot),'shared':len(shared)},'snapshot_feature_changes':feature_changes,'snapshot_columns_only_formal':sorted(set(formal_snapshot.columns)-set(asnapshot.columns)),'snapshot_columns_only_A':sorted(set(asnapshot.columns)-set(formal_snapshot.columns)),'interpretation':'Any formal-vs-A differences belong to the baseline reconstruction/context. Only A-vs-B deltas in ab_report.json isolate the 84-source universe repair.','formal_hash_preconditions_unchanged':True}
    ab.write_json(ab.OUT/'formal_baseline_comparison.json',report)
    ab.emit('formal_baseline_compare',rows=report['rows'],changed_recommendations=len(changes),changed_snapshot_features=len(feature_changes),stats={c:{k:v for k,v in s.items() if k!='largest'} for c,s in stats.items()})


if __name__=='__main__':
    main()
