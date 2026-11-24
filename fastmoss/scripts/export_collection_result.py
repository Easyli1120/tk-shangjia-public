"""Read-only, self-contained task report including local asset verification."""
import argparse
import datetime
import html
import json
from pathlib import Path
import sqlite3
import sys
from collections import Counter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tiktok_collect
import product_assets


def export(data,job_id,out):
    c=sqlite3.connect('file:'+str(data/'fastmoss.sqlite3')+'?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    job=dict(c.execute('SELECT * FROM tiktok_jobs WHERE id=?',(job_id,)).fetchone())
    rows=[]
    for item in c.execute('SELECT * FROM tiktok_items WHERE job_id=? ORDER BY position',(job_id,)).fetchall():
        row={k:item[k] for k in ('position','product_id','url','market','state','attempts','title','discovered','cached','error','updated_at')}
        row['missing']=json.loads(item['missing']);row['category']='';row['sku_count']=0;row['prices']=[];row['options']=[]
        capture=c.execute('SELECT product_json,audit_json FROM product_capture WHERE market=? AND product_id=?',(item['market'],item['product_id'])).fetchone()
        if capture and item['state'] in ('complete','partial'):
            product=json.loads(capture['product_json']);quality=json.loads(capture['audit_json'])
            row['category']=product.get('category','');row['sku_count']=len(product.get('skus',[]))
            row['prices']=sorted({str(v.get('price'))+' '+str(v.get('currency','')) for v in quality.get('variants',[])})
            row['options']=[{'name':v.get('prop_name',''),'values':[x.get('prop_value','') for x in v.get('sale_prop_values',[])]} for v in product.get('sale_props',[])]
            imgs=[dict(r) for r in c.execute('''SELECT i.url,i.local_path,g.group_name FROM gallery_jobs j JOIN gallery_items g ON g.job_id=j.id
              JOIN images i ON i.market=j.market AND i.product_id=j.product_id AND i.url=g.url
              WHERE j.market=? AND j.product_id=? ORDER BY g.position''',(item['market'],item['product_id']))]
            issues=[];valid=0
            for img in imgs:
                path=(data/(img.get('local_path') or '').lstrip('/')).resolve()
                base=(data/'images').resolve()
                if not img.get('local_path') or base not in path.parents or not path.is_file():
                    issues.append('部分本地图片缺失');img['local_path']=None;continue
                with path.open('rb') as f:header=f.read(32)
                if not product_assets.image_extension(header):issues.append('部分本地图片格式无效');img['local_path']=None;continue
                valid+=1
            checked=tiktok_collect.audit(product,imgs)
            expected={product_assets.asset_key(x['url']) for x in tiktok_collect.manifest(product)}
            actual={product_assets.asset_key(x['url']) for x in imgs if x.get('local_path')}
            if expected-actual:issues.append('来源图组未全部保存')
            row['verified_images']=valid
            row['validation_issues']=list(dict.fromkeys(issues+checked['missing']))
            row['image_groups']=dict(Counter(x['group_name'] for x in imgs))
        else:row['validation_issues']=[];row['verified_images']=0
        row['complete']=row['state']=='complete' and not row['validation_issues']
        row['reason']='；'.join(dict.fromkeys(([row['error']] if row['error'] else [])+row['missing']+row['validation_issues']))
        if not row['complete'] and not row['reason']:row['reason']='当前状态：'+row['state']
        rows.append(row)
    complete=[r for r in rows if r['complete']];incomplete=[r for r in rows if not r['complete']]
    stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')
    result={'job_id':job_id,'job_state':job['state'],'generated_at':stamp,'total':len(rows),'complete':len(complete),'incomplete':len(incomplete),'verified_images':sum(r['verified_images'] for r in complete),'skus':sum(r['sku_count'] for r in complete),'items':rows}
    out.mkdir(parents=True,exist_ok=True)
    (out/'采集明细.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    for name,items in [('完整采集链接.txt',complete),('未完成链接.txt',incomplete)]:
        (out/name).write_text(''.join(r['url']+'\n' for r in items))
    esc=lambda v:html.escape(str(v or ''))
    def table(items):
        if not items:return '<p>无</p>'
        heading='<tr><th>序号</th><th>商品</th><th>图片</th><th>SKU</th><th>价格</th><th>类目</th><th>结果 / 原因</th></tr>'
        body=''.join('<tr><td>'+str(r['position'])+'</td><td><a href="'+esc(r['url'])+'">'+esc(r['title'] or r['product_id'])+'</a><small>'+esc(r['product_id'])+'</small></td><td>'+str(r['verified_images'])+'/'+str(r['discovered'])+'</td><td>'+str(r['sku_count'])+'</td><td>'+esc(' / '.join(r['prices']))+'</td><td>'+esc(r['category'])+'</td><td>'+esc('完整保存' if r['complete'] else r['reason'])+'</td></tr>' for r in items)
        return '<table><thead>'+heading+'</thead><tbody>'+body+'</tbody></table>'
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>100 条 TikTok 商品采集结果</title><style>body{font:14px/1.65 system-ui,sans-serif;color:#263146;background:#f6f7fb;margin:36px}main{max-width:1400px;margin:auto}h1{font-size:28px}h2{margin-top:32px}p{color:#59677d}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border:1px solid #dce1e9;vertical-align:top}th{background:#ecedf8}td:nth-child(2){max-width:430px}small{display:block;color:#7b8494}a{color:#514ab0}section{padding:20px;background:white;border-radius:12px}.ok{color:#19826a}.bad{color:#a55432}</style><main>'''
    page+='<h1>100 条 TikTok 商品采集结果</h1><p>任务 #'+str(job_id)+' · 生成时间 '+esc(stamp)+' · 状态 '+esc(job['state'])+'</p>'
    page+='<section><strong>共 '+str(len(rows))+' 条</strong>　<span class="ok">完整采集 '+str(len(complete))+' 条</span>　<span class="bad">未完成 '+str(len(incomplete))+' 条</span><p>完整商品共 '+str(result['verified_images'])+' 张本地图片、'+str(result['skus'])+' 个 SKU。完整性以对应页面返回的图组、SKU、价格和类目为准，并核对本地文件存在及格式。</p></section>'
    page+='<h2>未完成及原因</h2>'+table(incomplete)+'<h2>完整采集</h2>'+table(complete)+'</main></html>'
    (out/'采集结果.html').write_text(page)
    c.close()
    return {k:v for k,v in result.items() if k!='items'}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--job',type=int,required=True);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(export(args.data,args.job,args.out),ensure_ascii=False))
