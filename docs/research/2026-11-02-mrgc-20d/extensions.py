"""Exploratory diagnostics declared AFTER primary result; never independent validation."""
import csv
import json
import statistics as st
from collections import defaultdict
import analyze as a


def main():
    data=a.load()
    data['day_index']={d:i for i,d in enumerate(data['days'])}
    feats,anomalies=a.features(data)
    data['anomaly_keys']={(r['symbol'],r['date']) for r in anomalies}
    cands,hits,daily,exits=a.candidates(data,feats)
    index=data['indices']
    gate={}
    for i,d in enumerate(data['days']):
        days=data['days']
        if i<24 or any(x not in index for x in days[i-24:i+1]):
            gate[d]=False
            continue
        ma=st.mean(index[x]['close'] for x in days[i-19:i+1])
        prev=st.mean(index[x]['close'] for x in days[i-24:i-4])
        gate[d]=index[d]['close']>ma and ma>prev
    recent=data['days'][-20:]
    m=[]
    for rule in a.RULES[:6]:
        for period in ('history','recent'):
            hist=set(json.loads((a.OUT/'audit.json').read_text())['history_dates'])
            subset=[r for r in cands[rule] if gate.get(r['signal_date']) and
                    (r['signal_date']>=a.START if period=='recent' else r['signal_date'] in hist)]
            records=a.simulate(subset,5,data)
            if period=='history':
                records=[dict(r,status='purged_boundary') if r.get('exit_date','')>=a.START else r for r in records]
            m.append(dict(rule=rule,period=period,**a.stats(records)))
    stocks=list(csv.DictReader((a.OUT/'stock_review.csv').open(encoding='utf-8-sig')))
    groups=defaultdict(list)
    for r in stocks:
        if r['first_net_return_5']:
            groups[r['industry']].append(float(r['first_net_return_5']))
    industry=[]
    for sector,vals in groups.items():
        industry.append(dict(industry=sector,n=len(vals),mean=st.mean(vals),win_rate=sum(x>0 for x in vals)/len(vals)))
    industry.sort(key=lambda r:r['mean'],reverse=True)
    rs=list(csv.DictReader((a.OUT/'trades.csv').open(encoding='utf-8-sig')))
    bydate=defaultdict(list)
    for r in rs:
        if r['period']=='recent' and r['rule']=='new5' and r['horizon']=='5' and r['managed']=='False' and r['status']=='closed':
            bydate[r['signal_date']].append(float(r['net_return']))
    dates=[dict(date=d,n=len(v),mean=st.mean(v),win_rate=sum(x>0 for x in v)/len(v)) for d,v in sorted(bydate.items())]
    recentbars=[index[d] for d in recent]
    a.dump('extensions.json',dict(status='EXPLORATORY_AFTER_PRIMARY',
       gate_definition='沪深300收盘高于MA20且MA20高于5交易日前MA20，仅已知收盘决定次日资格',
       recent_gate_days=[d for d in recent if gate[d]],metrics=m,
       first_hit_by_industry=industry,new5_by_signal_date=dates,
       benchmark_window_open_to_close=recentbars[-1]['close']/recentbars[0]['open']-1,
       benchmark_signal_close_to_last_close=recentbars[-1]['close']/recentbars[0]['close']-1))
    print(json.dumps({'gate_days':[d for d in recent if gate[d]],'industry':industry,'dates':dates},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
