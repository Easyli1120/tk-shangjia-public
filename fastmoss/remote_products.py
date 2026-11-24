"""Read existing platform products and edit their title/description explicitly."""
import hashlib
import html
from html.parser import HTMLParser
import json
import re

import tiktok_shop as shop


class PlainDescription(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]
    def handle_data(self, text): self.parts.append(text)
    def handle_endtag(self, tag):
        if tag in ('p','div','li'): self.parts.append('\n')
    def handle_starttag(self, tag, attrs):
        if tag == 'br': self.parts.append('\n')


def revision(context, product):
    value = [context, product.get('id'), product.get('title'), product.get('description'), product.get('update_time'), product.get('status')]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@shop.locked
def handle(data, action, body):
    product_id = str(body.get('product_id', ''))
    if not re.fullmatch(r'[0-9]{1,30}', product_id):
        raise shop.ShopError('商品编号无效')
    cfg = shop.load(data)
    context = {k: str(cfg.get(k, '')) for k in ('app_key','service_id','selected_shop_id')}
    selected = next((s for s in cfg.get('shops',[]) if str(s['id']) == context['selected_shop_id']), None)
    if not selected: raise shop.ShopError('请先选择已绑定店铺')
    if action == 'products-edit' and str(body.get('shop_id','')) != context['selected_shop_id']:
        raise shop.ShopError('上架店铺已改变，请重新打开商品')
    product = shop.api(data, '/product/202309/products/' + product_id, context=context)
    if str(product.get('id','')) != product_id:
        raise shop.ShopError('平台未返回匹配的商品，请重新查询')
    current = revision(context, product)
    if action == 'products-get':
        plain=PlainDescription();plain.feed(str(product.get('description','')))
        return {'product':product, 'description_text':''.join(plain.parts).strip(), 'shop_id':selected['id'], 'shop_name':selected.get('name',''), 'region':selected.get('region',''), 'revision':current}
    if selected.get('region') != 'US':
        raise shop.ShopError('当前编辑流程只支持美国店铺')
    if body.get('confirmed') is not True or body.get('revision') != current:
        raise shop.ShopError('商品已变化或尚未确认，请重新打开并核对后提交')
    title = str(body.get('title','')).strip()
    update_description = body.get('update_description') is True
    description = str(body.get('description_text','')).strip() if update_description else str(product.get('description',''))
    if not 1 <= len(title) <= 255 or (update_description and not 1 <= len(description) <= 10000):
        raise shop.ShopError('标题须为 1–255 字符，详情须为 1–10000 字符')
    if title == product.get('title') and not update_description:
        return {'ok':True,'message':'内容未改变，无需提交'}
    # Retain the current listing mode; never silently turn a live product into a draft.
    payload = {'title':title,'category_version':'v2',
               'save_mode':'AS_DRAFT' if product.get('status') == 'DRAFT' else 'LISTING'}
    if update_description:
        payload['description']=''.join('<p>'+html.escape(line)+'</p>' for line in description.splitlines() if line.strip())
        if len(payload['description'])>10000: raise shop.ShopError('详情内容过长，请缩短后重试')
    shop.api(data, '/product/202509/products/' + product_id + '/partial_edit', payload=payload, context=context)
    return {'ok':True,'message':'平台已接受修改请求，请在 Seller Center 核对更新及审核状态'}
