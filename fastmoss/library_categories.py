"""Classify library products independently of collection and publishing permissions."""
import json
import re
import threading
import time

import listing_editor
import shop_listing as listing
import tiktok_collect

PENDING = '待识别末级类目'
SKIPPED = '已跳过（本地区不售）'
_new_capture = threading.Event()
INPUT_KEY = "json_array(3, r.snapshot_id, r.category, COALESCE(NULLIF(le.title,''),r.name), r.shop_name, cap.received_at, (SELECT MAX(e.id) FROM draft_events e WHERE e.market=r.market AND e.product_id=r.id))"
JOINS = """
 LEFT JOIN listing_edits le ON le.market=r.market AND le.id=r.id
 LEFT JOIN product_capture cap ON cap.market=r.market AND cap.product_id=r.id
 LEFT JOIN library_leaf_categories leaf ON leaf.market=r.market AND leaf.product_id=r.id
    AND leaf.input_key=""" + INPUT_KEY
PROFILE_JOIN = " LEFT JOIN shop_listing_profiles cp ON cp.market=r.market AND cp.product_id=r.id"


def migrate(c):
    c.execute('CREATE INDEX IF NOT EXISTS draft_events_product ON draft_events(market,product_id,id)')
    c.execute("""CREATE TABLE IF NOT EXISTS library_leaf_categories(
        market TEXT, product_id TEXT, input_key TEXT NOT NULL, path TEXT NOT NULL,
        retry_at INTEGER NOT NULL, PRIMARY KEY(market,product_id))""")


def current_input(c, market, eid):
    row=c.execute('SELECT '+INPUT_KEY+' FROM records r JOIN draft_entries d '
                  'ON d.market=r.market AND d.id=r.id '+JOINS+
                  " WHERE r.kind='product' AND d.active=1 AND r.market=? AND r.id=?",(market,eid)).fetchone()
    return row[0] if row else None


def still_current(server, item):
    with server.db() as c:
        return current_input(c,item['market'],item['id'])==item['input_key']


def saved_path(c, market, eid, shop_id):
    """Return the same current classification shown in the competitor library."""
    row=c.execute("SELECT COALESCE(CASE WHEN json_extract(cp.profile,'$.category_shop_id')=? "
                  "THEN NULLIF(json_extract(cp.profile,'$.category_path'),'') END,NULLIF(leaf.path,'')) "
                  "FROM records r " + JOINS + PROFILE_JOIN +
                  " WHERE r.kind='product' AND r.market=? AND r.id=?", (shop_id,market,eid)).fetchone()
    return row[0] or '' if row else ''


def classify(data, context, mode, categories, source, title, is_current=lambda:True):
    if not is_current():return '', 'cancelled'
    # The competitor can sell in branches unavailable to our own shop.
    # Permission checks still apply separately when the seller submits a listing.
    if tiktok_collect.confirm_category(source, categories):
        return source['category'], 'matched'
    ancestor = listing.source_category(source, categories, leaf_only=False, include_unavailable=True)
    nodes = {str(c['id']): c for c in categories}
    if ancestor and nodes[ancestor].get('is_leaf'):
        return listing.category_path(ancestor, categories), 'matched'
    candidates = (listing.descendant_categories(ancestor, categories, include_unavailable=True) if ancestor else
                  {cid for cid, node in nodes.items() if node.get('is_leaf')})
    cid = listing.refine_source_category(title, ancestor, categories, include_unavailable=True) if ancestor else ''
    if not cid and ancestor:
        # Match an explicit leaf name in the title only when it singles out one
        # descendant. The source branch already supplies the audience context.
        matches = []
        has_shorts = re.search(r'\bshorts\b', title, re.I)
        has_pants = re.search(r'\b(?:pants|trousers|sweatpants|joggers)\b', title, re.I)
        has_top = re.search(r'\b(?:tops?|hoodies?|shirts?|sweatshirts?|jackets?)\b', title, re.I)
        audience = {word for word in ('women', 'men', 'boys', 'girls') if
                    re.search(r'\b' + word + r"(?:['’]?s)?\b", title, re.I)}
        for candidate in candidates:
            label = (nodes[candidate].get('english_name') or '').lower()
            prefix = re.match(r"^(men|women|boys|girls)(?:'s|')\s+", label)
            if prefix:
                if audience and prefix[1] not in audience:
                    continue
                label = label[prefix.end():]
            if label in ('shorts & top sets','pants & top sets') and has_top and bool(has_shorts) != bool(has_pants):
                if label.startswith('shorts' if has_shorts else 'pants'):
                    matches.append(candidate)
                continue
            singular = label[:-1] if label.endswith('s') and not label.endswith('ss') else label
            names = [singular]
            if label.startswith('trench & rain '):
                names.extend(['trench coat', 'rain jacket', 'raincoat'])
            elif label == 'sports t-shirt':
                names.extend(['t-shirt', 'tshirt', 'tee'])
            elif label == 'fleece jackets' and re.search(r'\bfleece[ -]lined\b', title, re.I):
                names.append('jacket')
            elif label in ('tracksuit set', 'sportswear set') and audience and prefix:
                if re.search(r'\btracksuits?\b', title, re.I):
                    if label == 'tracksuit set':
                        matches.append(candidate)
                elif label == 'sportswear set' and re.search(r'\bsports? sets?\b', title, re.I):
                    matches.append(candidate)
                continue
            if len(label) >= 3 and any(re.search(r'\b' + re.escape(name) + r's?\b', title, re.I) for name in names):
                matches.append(candidate)
        if len(matches) == 1:
            cid = matches[0]
    if not cid and ancestor:
        parent_label = str(nodes.get(ancestor, {}).get('english_name') or
                           nodes.get(ancestor, {}).get('local_name') or '').casefold()
        title_lower = title.casefold()
        def pick(pattern):
            found = [key for key in candidates if re.search(pattern, str(nodes[key].get('english_name','')), re.I)]
            return found[0] if len(found) == 1 else ''
        if parent_label == 'sports outerwear':
            cid = pick(r"men's sports (?:coat|jacket)") or pick(r"women's sports (?:coat|jacket)")
        elif parent_label in ('sports t-shirt', 'sports t-shirts', '运动 t恤'):
            if re.search(r'hood(?:ie|ies|ed)', title_lower):
                if re.search(r"women'?s?", title_lower): cid = pick(r"women's sports hoodie")
                elif re.search(r"men'?s?", title_lower): cid = pick(r"men's sports hoodie")
            if not cid:
                if re.search(r"women'?s?", title_lower): cid = pick(r"women's sports sweatshirt")
                elif re.search(r"men'?s?", title_lower): cid = pick(r"men's sports sweatshirt")
        elif 'party' in parent_label or 'accessor' in parent_label:
            # A parent breadcrumb can be polluted by the page's recommendation
            # rail. The title's garment type is stronger evidence here.
            cid = pick(r"hoodie") or pick(r"sweatshirt")
    status = 'matched'
    if not cid:
        cid, status = listing.recommend_category(data, context, title, mode, candidates, source, is_current=is_current)
    if not cid and ancestor and status == 'no_match':
        # Some source breadcrumbs describe a different product type entirely.
        # Ask for a title-based recommendation and verify it is an official leaf.
        all_leaves = {key for key, node in nodes.items() if node.get('is_leaf')}
        cid, status = listing.recommend_category(data, context, title, mode, all_leaves, is_current=is_current)
    return (listing.category_path(cid, categories) if cid else ''), status


def classify_pending(server, retry_missing=False):
    shop_id = str(listing.shop.load(server.DATA).get('selected_shop_id') or '')
    with server.db() as c:
        items = c.execute("""
            SELECT r.market,r.id,COALESCE(NULLIF(le.title,''),r.name) title,r.shop_name,
                   r.category,""" + INPUT_KEY + """ input_key
            FROM records r JOIN draft_entries d ON d.market=r.market AND d.id=r.id
            """ + JOINS + PROFILE_JOIN + """
            WHERE r.kind='product' AND d.active=1
              AND (leaf.retry_at IS NULL OR (leaf.path='' AND (leaf.retry_at<=? OR ?)))
              AND NOT EXISTS(SELECT 1 FROM tiktok_product_availability a WHERE
                  a.market=r.market AND a.product_id=r.id AND a.state='regional_unavailable')
              AND NOT COALESCE(json_extract(cp.profile,'$.category_shop_id')=?
                  AND NULLIF(json_extract(cp.profile,'$.category_path'),'') IS NOT NULL,0)
            ORDER BY r.snapshot_id DESC,r.id""", (int(time.time()),retry_missing,shop_id)).fetchall()
    categories = None
    shop_error = None
    for item in items:
        key = (item['market'], item['id'])
        is_current = lambda: still_current(server,item)
        with server.db() as c:
            if current_input(c,*key)!=item['input_key']:continue
            row = c.execute('SELECT product_json FROM product_capture WHERE market=? AND product_id=?', key).fetchone()
        source = json.loads(row[0]) if row else {}
        source['category'] = source.get('category') or item['category'] or ''
        if tiktok_collect.confirmed_leaf(source):
            path, status = source['category'], 'matched'
        elif shop_id and item['market'] == 'US':
            if shop_error:
                continue
            if categories is None:
                try:
                    context, _ = listing.identity(server.DATA)
                    if not is_current():continue
                    base = listing.metadata(server.DATA, context)
                    if not is_current():continue
                    categories = listing.bilingual_categories(server.DATA, context, base)
                except listing.shop.ShopError as exc:
                    shop_error = exc
                    continue
            path, status = classify(server.DATA, context, base['mode'], categories, source,
                                    listing_editor.clean_title(item['title'], item['shop_name']),is_current=is_current)
        else:
            continue
        retry = 300 if status == 'unavailable' else 86400
        with server.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if current_input(c,*key)!=item['input_key']:continue
            c.execute('INSERT OR REPLACE INTO library_leaf_categories VALUES(?,?,?,?,?)',
                      (*key, item['input_key'], path, int(time.time()) + retry))
    if shop_error:
        raise shop_error


def notify_saved():
    _new_capture.set()


def start(server):
    def run():
        first_pass = True
        while True:
            _new_capture.clear()
            try:
                classify_pending(server, retry_missing=first_pass)
                first_pass = False
                _new_capture.wait(15)
            except Exception as exc:
                print('竞品末级类目识别暂缓：' + str(exc), flush=True)
                time.sleep(60)
    threading.Thread(target=run, daemon=True, name='library-leaf-categories').start()
