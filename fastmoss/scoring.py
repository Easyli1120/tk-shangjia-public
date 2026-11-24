"""Versioned, deterministic relative product ranking."""
import bisect, math

VERSION='product-ranking-v2'
WEIGHTS={'gmv7':20,'sales7':20,'gmv28':15,'sales28':15,'gmv_total':10,'sales_total':10,'momentum_sales':2.5,'momentum_gmv':2.5,'creators':5/3,'videos':5/3,'livestreams':5/3}
METRICS=('gmv7','sales7','gmv28','sales28','gmv_total','sales_total','creators','videos','livestreams')

def valid(value):
    try:
        value=float(value)
        return value if math.isfinite(value) and value>=0 else None
    except (ValueError,TypeError):return None

def rank_products(products):
    rows=[]
    for source in products:
        p=dict(source)
        values={k:valid(p.get(k)) for k in METRICS}
        for key,a,b in [('momentum_sales','sales7','sales28'),('momentum_gmv','gmv7','gmv28')]:
            # A zero denominator makes the ratio undefined, including 0/0.
            values[key]=values[a]/(values[b]/4) if values[a] is not None and values[b] is not None and values[b]>0 else None
        p['values']=values;rows.append(p)
    # log1p is monotonic: ranks are unchanged, so explicit clipping is unnecessary.
    distributions={k:sorted(math.log1p(p['values'][k]) for p in rows if p['values'][k] is not None) for k in WEIGHTS}
    eligible=[]
    for p in rows:
        detail={}; weighted=available=0
        for k,w in WEIGHTS.items():
            v=p['values'][k];arr=distributions[k]
            if v is None or not arr:
                detail[k]={'value':None,'percentile':None,'weight':w};continue
            z=math.log1p(v);lo=bisect.bisect_left(arr,z);hi=bisect.bisect_right(arr,z)
            percentile=100*(lo+hi)/(2*len(arr))
            detail[k]={'value':v,'percentile':percentile,'weight':w}
            weighted+=percentile*w;available+=w
        has_sales=any(p['values'][k] is not None for k in METRICS[:6])
        p.update(components=detail,completeness=available,score_exact=weighted/available if has_sales and available else None,rank=None,band='数据不足')
        if p['score_exact'] is not None:eligible.append(p)
    def tie(p,k):
        v=p['values'][k]
        return (v is None,-v if v is not None else 0)
    eligible.sort(key=lambda p:(-p['score_exact'],*(z for k in ('gmv7','sales7','gmv28','sales28','sales_total') for z in tie(p,k)),str(p['id']),str(p['market'])))
    a=math.ceil(len(eligible)*.30);b=math.ceil(len(eligible)*.50)
    for i,p in enumerate(eligible,1):
        p['rank']=i;p['band']='自动入箱' if i<=a else '人工筛选' if i<=b else '低优先级'
    return rows
