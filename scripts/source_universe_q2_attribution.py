"""Explain the material B-to-C source repair sensitivity without new inference."""
from pathlib import Path
import json
import os
import pickle
import numpy as np
import pandas as pd
from scripts import source_universe_snapshot_ab as ab


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Physical acceptance workspace only')
    from ml import predict
    from ml.features.registry import FEATURE_REGISTRY
    old=ab.OUT/'verified_rerun';new=ab.OUT/'quarter_C'
    snapshots={'B':pd.read_pickle(old/'snapshot_B.pkl'),'C':pd.read_pickle(new/'snapshot_C.pkl')}
    preds={'B':pd.read_csv(old/f'predictions_B_{ab.ASOF}.csv',dtype={'ticker':str}),'C':pd.read_csv(new/f'predictions_C_{ab.ASOF}.csv',dtype={'ticker':str})}
    joined=preds['B'].merge(preds['C'],on=['ticker','date'],suffixes=('_B','_C'),validate='one_to_one')
    delta=joined.prob_edge_C-joined.prob_edge_B
    top=joined.loc[delta.abs().nlargest(10).index,'ticker'].tolist()
    raw={branch:pd.concat([row for row in pickle.loads(path.read_bytes()).values() if row is not None],ignore_index=True).set_index('ticker') for branch,path in [('B',old/'raw_feature_rows.pkl'),('C',new/'raw_feature_rows_C.pkl')]}
    groups={column:name for name,item in FEATURE_REGISTRY.items() for column in item['columns']}
    summaries=[]
    for column in sorted(set(raw['B'])&set(raw['C'])):
        if not pd.api.types.is_numeric_dtype(raw['B'][column]):
            continue
        left,right=raw['B'][column],raw['C'].loc[raw['B'].index,column]
        equal=left.eq(right)|(left.isna()&right.isna())
        if equal.all():
            continue
        summaries.append({'feature':column,'group':groups.get(column,'other'),'changed_rows':int((~equal).sum()),'B_missing':int(left.isna().sum()),'C_missing':int(right.isna().sum()),'B_mean':float(left.mean()),'C_mean':float(right.mean()),'B_std':float(left.std()),'C_std':float(right.std()),'B_max':float(left.max()),'C_max':float(right.max()),'B_min':float(left.min()),'C_min':float(right.min())})
    model,meta=predict.load_selected_model(slot='production')
    columns=meta['feature_columns']
    frames={}
    for branch,snapshot in snapshots.items():
        normalized=predict.apply_snapshot_zscore(snapshot) if predict._model_needs_zscore(meta) else snapshot
        normalized=normalized.set_index('ticker')
        frames[branch]=normalized.reindex(index=top,columns=columns)
    contributions={branch:model.predict(frame.values,pred_contrib=True).reshape(len(top),3,len(columns)+1) for branch,frame in frames.items()}
    differences=(contributions['C'][:,2,:]-contributions['C'][:,0,:])-(contributions['B'][:,2,:]-contributions['B'][:,0,:])
    cases=[]
    for i,ticker in enumerate(top):
        row=joined[joined.ticker.eq(ticker)].iloc[0]
        terms={column:float(differences[i,n]) for n,column in enumerate(columns)}
        aggregated={}
        for column,value in terms.items():
            group=groups.get(column,'other');aggregated[group]=aggregated.get(group,0.)+value
        cases.append({'ticker':ticker,'prob_edge_delta':float(row.prob_edge_C-row.prob_edge_B),'recommendation_B':row.recommendation_B,'recommendation_C':row.recommendation_C,'top_logit_contribution_deltas':sorted(terms.items(),key=lambda x:abs(x[1]),reverse=True)[:10],'group_logit_deltas':sorted(aggregated.items(),key=lambda x:abs(x[1]),reverse=True)})
    a=snapshots['B'].set_index('ticker');b=snapshots['C'].set_index('ticker').loc[a.index]
    unchanged={column:bool((a[column].eq(b[column])|(a[column].isna()&b[column].isna())).all()) for column in ['Close','Open','High','Low','Volume','sector_id',*FEATURE_REGISTRY['market_regime']['columns']] if column in a and column in b}
    report={'branch':'C','baseline':'B','raw_feature_distribution_changes':summaries,'top10_probability_cases':cases,'unchanged_price_category_market_inputs':unchanged,'model_sha256':ab.digest(meta['model_file']),'limits':['Group differences are UP-minus-DOWN raw logit contributions, not additive probability percentage points.','Source corrections change global cross-sectional scaling; this does not establish improved realized performance or calibrated confidence.','Official source truth, model response and future model validation are distinct acceptance questions.']}
    ab.write_json(new/'q2_input_attribution.json',report)
    ab.emit('q2_attribution_complete',raw_changed_features=len(summaries),unchanged=unchanged,top10=top)


if __name__=='__main__':
    main()
