"""Attribute verified A/B probability changes with the original pinned booster."""
from pathlib import Path
import json
import os

import numpy as np
import pandas as pd

from scripts import source_universe_snapshot_ab as ab


def main():
    if Path(os.environ.get('STOCK_ACCEPTANCE_ROOT','')).resolve()!=ab.BASE:
        raise RuntimeError('Physical acceptance workspace only')
    from ml import predict
    from ml.features.registry import FEATURE_REGISTRY
    out=ab.OUT/'verified_rerun'
    manifest=json.loads((out/'frozen_manifest.json').read_text(encoding='utf-8'))
    ab.verify_files(manifest['files'])
    model,meta=predict.load_selected_model(slot='production')
    columns=meta['feature_columns']
    snapshots={branch:pd.read_pickle(out/f'snapshot_{branch}.pkl').set_index('ticker',drop=False) for branch in ['A','B']}
    predictions={branch:pd.read_csv(out/f'predictions_{branch}_{ab.ASOF}.csv',dtype={'ticker':str}) for branch in ['A','B']}
    joined=predictions['A'].merge(predictions['B'],on=['ticker','date'],suffixes=('_A','_B'),validate='one_to_one')
    delta=joined.prob_edge_B-joined.prob_edge_A
    top5=joined.loc[delta.abs().nlargest(5).index,'ticker'].tolist()
    changed=joined.recommendation_A.ne(joined.recommendation_B)
    boundary=changed & (joined.recommendation_A.str.contains('買進|觀望')|joined.recommendation_B.str.contains('買進|觀望'))
    buy_watch=changed & ((joined.recommendation_A.str.contains('買進')&joined.recommendation_B.str.contains('觀望'))|(joined.recommendation_B.str.contains('買進')&joined.recommendation_A.str.contains('觀望')))
    tickers=sorted(set(top5)|set(joined.loc[boundary,'ticker']))
    frames={}
    for branch,snapshot in snapshots.items():
        transformed=predict.apply_snapshot_zscore(snapshot.reset_index(drop=True)) if predict._model_needs_zscore(meta) else snapshot.reset_index(drop=True)
        transformed=transformed.set_index('ticker')
        frames[branch]=pd.DataFrame({col:transformed.loc[tickers,col].values if col in transformed else np.full(len(tickers),np.nan) for col in columns},index=tickers)
    contributions={branch:model.predict(frame.values,pred_contrib=True).reshape(len(tickers),3,len(columns)+1) for branch,frame in frames.items()}
    proba={branch:model.predict(frame.values) for branch,frame in frames.items()}
    for branch in ['A','B']:
        logits=contributions[branch].sum(axis=2)
        softmax=np.exp(logits-logits.max(axis=1,keepdims=True));softmax/=softmax.sum(axis=1,keepdims=True)
        np.testing.assert_allclose(softmax,proba[branch],atol=1e-12,rtol=1e-12)
        source=predictions[branch].set_index('ticker').loc[tickers]
        np.testing.assert_allclose(proba[branch][:,2]-proba[branch][:,0],source.prob_edge,atol=1e-12,rtol=1e-12)
    groups={col:name for name,item in FEATURE_REGISTRY.items() for col in item['columns']}
    groups['sector_id']='sector_category_id'
    contribution_delta=(contributions['B'][:,2,:]-contributions['B'][:,0,:])-(contributions['A'][:,2,:]-contributions['A'][:,0,:])
    rows=[]
    for i,ticker in enumerate(tickers):
        case=joined[joined.ticker==ticker].iloc[0]
        row={'ticker':ticker,'date':case.date,'top5_abs_prob':ticker in top5,'recommendation_A':case.recommendation_A,'recommendation_B':case.recommendation_B,'prob_edge_A':float(case.prob_edge_A),'prob_edge_B':float(case.prob_edge_B),'prob_edge_delta':float(case.prob_edge_B-case.prob_edge_A)}
        changes=[];group_changes={};group_contributions={}
        for number,col in enumerate(columns):
            a,b=frames['A'].loc[ticker,col],frames['B'].loc[ticker,col]
            group=groups.get(col,'other')
            group_contributions[group]=group_contributions.get(group,0.)+float(contribution_delta[i,number])
            if (pd.isna(a) and pd.isna(b)) or a==b:
                continue
            difference=float(b-a) if pd.notna(a) and pd.notna(b) else None
            item={'feature':col,'group':group,'input_A':float(a) if pd.notna(a) else None,'input_B':float(b) if pd.notna(b) else None,'input_delta':difference,'contribution_UP_minus_DOWN_logit_delta':float(contribution_delta[i,number])}
            changes.append(item)
            group_changes[group]=group_changes.get(group,0)+1
        row['changed_model_input_groups']=group_changes
        row['top_input_changes_by_contribution']=sorted(changes,key=lambda x:abs(x['contribution_UP_minus_DOWN_logit_delta']),reverse=True)[:12]
        row['group_contribution_logit_deltas']=dict(sorted(group_contributions.items(),key=lambda x:abs(x[1]),reverse=True))
        row['bias_logit_delta']=float(contribution_delta[i,-1])
        rows.append(row)
    a=snapshots['A'];b=snapshots['B'].loc[a.index]
    same_id=a.sector_id.eq(b.sector_id)|(a.sector_id.isna()&b.sector_id.isna())
    sector_map=pd.read_csv(ab.BASE/'ml/data/sector_mapping.csv',dtype={'Ticker':str}).set_index('Ticker').Sector
    maps={branch:frame[['ticker','sector_id']].assign(sector=frame.ticker.map(sector_map)).drop_duplicates(['sector','sector_id']).to_dict('records') for branch,frame in snapshots.items()}
    regime_cols=['market_breadth_20d_pct','taiex_vol_pct','taiex_volatility_pct']
    actual_regime=[col for col in a if col in FEATURE_REGISTRY['market_regime']['columns']]
    regime_changes={col:{'changed_existing_rows':int((~(a[col].eq(b[col])|(a[col].isna()&b[col].isna()))).sum()),'A_9_4':a.loc[a.Date.astype(str).eq(ab.ASOF),col].unique().tolist(),'B_9_4':b.loc[b.Date.astype(str).eq(ab.ASOF),col].unique().tolist()} for col in actual_regime}
    report={'status':'VERIFIED_AB_INPUT_ATTRIBUTION','top5_abs_prob_tickers':top5,'all_buy_watch_flips':joined.loc[buy_watch,['ticker','recommendation_A','recommendation_B']].to_dict('records'),'expanded_buy_or_watch_boundary_cases':len(rows),'sector_category_id_changes_existing':int((~same_id).sum()),'sector_category_maps':maps,'raw_market_regime_changes':regime_changes,'cases':rows,'base_model_sha256':ab.digest(meta['model_file']),'probabilities_reproduced_exact_tolerance':1e-12,'limits':['Contributions explain class UP-minus-DOWN raw logit changes; probability changes are nonlinear and these are not percentage-point contribution shares.','Input deltas reflect canonical branch cross-sectional processing including model-required z-score. No new threshold or algorithm is introduced.','This is source sensitivity attribution, not realized returns or evidence of KOL endorsement.']}
    ab.write_json(out/'ab_input_attribution.json',report)
    ab.emit('ab_attribution_complete',top5=top5,buy_watch_flips=int(buy_watch.sum()),expanded_cases=len(rows),sector_id_changes=int((~same_id).sum()),regime_changes=regime_changes)


if __name__=='__main__':
    main()
