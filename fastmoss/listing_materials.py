"""Conservative fabric checks against current copy and captured specifications."""
import hashlib
import json
import re


ALIASES = {
    'Cotton': r'cotton|棉', 'Polyester': r'polyester|涤纶|聚酯纤维',
    'Elastane': r'elastane|spandex|lycra|氨纶', 'Nylon': r'nylon|polyamide|尼龙|锦纶',
    'Viscose': r'viscose|rayon|粘胶|人造丝', 'Linen': r'linen|flax|亚麻',
    'Wool': r'wool|羊毛', 'Silk': r'silk|蚕丝|真丝',
    'Acrylic': r'acrylic|腈纶', 'Modal': r'modal|莫代尔',
    'Lyocell': r'lyocell|tencel|莱赛尔|天丝', 'Cashmere': r'cashmere|羊绒',
    'Bamboo': r'bamboo|竹纤维', 'Hemp': r'hemp|大麻纤维', 'Acetate': r'acetate|醋酸纤维',
    'Latex': r'latex|乳胶', 'Rubber': r'(?:natural\s+)?rubber|(?:天然)?橡胶',
    'Neoprene': r'neoprene|氯丁橡胶',
}
PATTERNS = {name: re.compile(r'(?<![a-z])(?:' + aliases + r')(?![a-z])', re.I)
            for name, aliases in ALIASES.items()}
FABRIC_NAMES = {'material', 'materials', 'fabric', 'fabric composition',
                'material composition', 'fabric content', '材质', '面料', '面料成分'}
GARMENTS = {'tshirt': r'\bt[ -]?shirts?\b|\btees?\b|T恤',
            'sweatshirt': r'\bhoodies?\b|\bsweatshirts?\b|\bjumpers?\b|卫衣'}
UNCERTAIN = re.compile(r'\b(?:not|no|without|free|like|look|feel|feels|feeling|imitation|faux|lining|shell|'
                       r'outer|inner|layer|layers|heather|except|depending|varies|other fibers?|other fibres?)\b|仿|不含|里料|外层|内层|不同颜色', re.I)


def is_material(rule):
    return (rule.get('type') == 'PRODUCT_PROPERTY' and
            str(rule.get('name', '')).strip().casefold() in FABRIC_NAMES)


def garment_types(text):
    return {kind for kind, pattern in GARMENTS.items() if re.search(pattern, text, re.I)}


def parse(text, strict=False):
    materials = []
    percentages = {}
    first = min((match.start() for pattern in PATTERNS.values() for match in pattern.finditer(text)), default=len(text))
    suffix_percentages = not re.search(r'\d\s*%\s*$', text[:first])
    for name, pattern in PATTERNS.items():
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        materials.append(name)
        amounts = set()
        for match in matches:
            before = re.search(r'(\d+(?:\.\d+)?)\s*%\s*$', text[:match.start()])
            after = re.match(r'\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%', text[match.end():])
            amount = (after or before) if suffix_percentages else (before or after)
            if amount:
                amounts.add(float(amount.group(1)))
        if len(amounts) > 1:
            return dict(materials=materials, percentages={}, uncertain=True)
        if amounts:
            percentages[name] = amounts.pop()
    # Preserve unknown components instead of treating the recognized subset as
    # the whole fabric. Percent claims also occur outside labelled spec fields.
    remainder=text
    for pattern in PATTERNS.values(): remainder=pattern.sub(' ',remainder)
    remainder=re.sub(r'\d+(?:\.\d+)?\s*%', ' ', remainder)
    remainder=re.sub(r'\b(?:material|materials|fabric|composition|content|blend|blended|and|with|of|pure)\b|材质|面料|成分|混纺|纯', ' ', remainder, flags=re.I)
    unknown=[]
    if strict:
        unknown=[part.strip() for part in re.split(r'[,/;+：:&]+',remainder) if re.search(r'[a-z\u4e00-\u9fff]',part,re.I)]
    for match in re.finditer(r'\d+(?:\.\d+)?\s*%\s*([a-z][a-z -]*)',text,re.I):
        if not any(pattern.match(match.group(1)) for pattern in PATTERNS.values()):
            unknown.append(match.group(1).strip())
    uncertain = bool(UNCERTAIN.search(text) or unknown)
    if len(materials) < 2 and re.search(r'\b(?:blend|blended|mixed|rich)\b|混纺',text,re.I):
        uncertain = True
    if percentages and (any(value <= 0 or value > 100 for value in percentages.values()) or
                        len(percentages) != len(materials) or sum(percentages.values()) > 100.01 or
                        (len(percentages) == len(materials) and abs(sum(percentages.values()) - 100) > .01)):
        uncertain = True
    return dict(materials=sorted(materials), percentages=percentages, uncertain=uncertain, unknown=list(dict.fromkeys(unknown)))


def evidence(text, label, garment, scoped=False):
    rows = []
    scope = set()
    for line in re.split(r'[\n;；]+|(?<=[.!?])\s+', str(text or '')):
        line = line.strip()
        if not line:
            continue
        kinds = garment_types(line)
        # Only explicit headings carry a scope to later lines.
        if scoped and kinds and not parse(line)['materials'] and line.endswith((':', '：')):
            scope = kinds
        kinds = kinds or scope
        explicit=label=='采集属性' or bool(re.match(r'^(?:material|fabric|composition|材质|面料)\s*[:：]',line,re.I))
        claim = parse(line, strict=explicit)
        if not claim['materials'] and not claim['unknown']:
            continue
        ignored = bool(scoped and garment and kinds and not garment.intersection(kinds))
        ambiguous = bool(scoped and kinds and len(garment) != 1 and not ignored)
        rows.append(dict(label=label, text=line[:600], ignored=ignored,
                         **dict(claim, uncertain=claim['uncertain'] or ambiguous)))
    return rows


def compatible(rows):
    if not rows or any(row['uncertain'] for row in rows):
        return False
    if len({tuple(row['materials']) for row in rows}) != 1:
        return False
    amounts = {}
    for row in rows:
        for name, amount in row['percentages'].items():
            if name in amounts and amounts[name] != amount:
                return False
            amounts[name] = amount
    return not amounts or sum(amounts.values()) <= 100.01


def map_values(materials, rule, percentages):
    """Use every matching official ID; never truncate a blend for a single select."""
    values = []
    for name in materials:
        match = next((v for v in rule.get('values', [])
                      if re.fullmatch(ALIASES[name], str(v.get('name', '')).strip(), re.I)), None)
        if not match:
            break
        values.append({'id': str(match['id'])})
    if len(values) == len(materials) and (len(values) == 1 or rule.get('is_multiple_selection')):
        return values
    # A category may expose a single official blend option.
    for option in rule.get('values', []):
        parsed = parse(str(option.get('name', '')))
        if (parsed['materials'] == materials and not parsed['uncertain'] and len(materials) > 1 and
                (not parsed['percentages'] or parsed['percentages'] == percentages)):
            return [{'id': str(option['id'])}]
    if rule.get('is_customizable'):
        return [{'name': ', '.join((('%g%% ' % percentages[name]) if name in percentages else '') + name for name in materials)}]
    return []


def review(source, title, description, attributes, rules, confirmation=''):
    material_rules = [rule for rule in rules if is_material(rule)]
    result = dict(applicable=bool(material_rules), status='unknown', evidence=[],
                  attribute_ids=[str(rule['id']) for rule in material_rules],
                  issues=[], suggested_attributes={}, automatic_attributes={}, suggestion='')
    result['verification_key']=hashlib.sha256(json.dumps([source.get('specifications',[]),title,description,attributes,material_rules],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if not material_rules:
        return result
    garment = garment_types(title)
    text_rows = evidence(title, '当前标题', garment) + evidence(description, '当前详情', garment, True)
    source_rows = []
    for spec in source.get('specifications', []):
        if str(spec.get('name', '')).strip().casefold() in FABRIC_NAMES:
            value=str(spec.get('value') or '').strip()
            if value:
                source_rows += evidence(value, '采集属性', garment) or [
                    dict(label='采集属性',text=value,ignored=False,materials=[],percentages={},uncertain=True)]
    active = [row for row in text_rows if not row['ignored']]
    rows = active or source_rows
    result['evidence'] = source_rows + text_rows
    selected_rows = []
    for rule in material_rules:
        names = {str(v['id']): str(v.get('name', '')) for v in rule.get('values', [])}
        values = attributes.get(str(rule['id'])) or []
        text = ', '.join(names.get(str(value.get('id')), str(value.get('name', ''))) for value in values)
        if text:
            row = dict(label='当前上架属性', text=text, ignored=False, attribute_id=str(rule['id']), **parse(text))
            selected_rows.append(row)
            result['evidence'].append(row)
    def issue(message):
        result['issues'].append(dict(kind='material_conflict', section='publish-material',
                                     label='面料交叉核对', message=message))
    if not rows:
        result['message'] = '未找到明确面料证据，请依据实物标签或供应商资料填写；不会推测成分。'
        return result
    if not compatible(rows):
        result.update(status='conflict', message='面料证据含未知成分、分层/款式差异或比例冲突，请按实物标签核实各层成分后修正文案，不能合并为单一混纺。')
        issue(result['message'])
        return result
    materials = rows[0]['materials']
    amounts = {name: value for row in rows for name, value in row['percentages'].items()}
    result['suggestion'] = ' / '.join((('%g%% ' % amounts[name]) if name in amounts else '') + name for name in materials)
    for rule in material_rules:
        values = map_values(materials, rule, amounts)
        if not values:
            issue('该类目的材质选项无法完整表示 ' + result['suggestion'] + '，请核实类目或向平台确认，不能只填第一种材质。')
        else:
            result['suggested_attributes'][str(rule['id'])] = values
    source_consistent = not source_rows or compatible(rows + source_rows)
    if source_rows and (any(row['uncertain'] for row in source_rows) or not compatible(source_rows)) and confirmation!=result['verification_key']:
        result.update(status='conflict',message='采集材质存在未知成分、分层成分或重复属性冲突。请按实物修正当前文案和材质，再确认来源冲突。',suggested_attributes={},source_conflict=True)
        issue(result['message'])
        return result
    if not source_consistent:
        result['source_warning'] = '采集属性与当前文案不同，请依据实物标签或供应商资料确认建议后填写。'
    for row in selected_rows:
        if not compatible(rows + [row]):
            issue('上架材质“' + row['text'] + '”与当前文案“' + result['suggestion'] + '”不一致，请核实后完整填写。')
    # Complete omissions only when two sources agree. An incomplete source
    # property needs agreement between the current title AND scoped description.
    # Explicit conflicting percentages or unrelated fibers are never overwritten.
    def supported_subset(row):
        return (bool(row['materials']) and not row['uncertain'] and
                set(row['materials']).issubset(materials) and
                all(amounts.get(name) == value for name,value in row['percentages'].items()))
    corroborated = len({row['label'] for row in active + source_rows}) >= 2 and source_consistent
    incomplete_source = ({row['label'] for row in active} == {'当前标题','当前详情'} and
                         all(supported_subset(row) for row in source_rows))
    can_complete = ((corroborated or incomplete_source) and
                    all(supported_subset(row) for row in selected_rows) and
                    len(result['suggested_attributes']) == len(material_rules))
    selected = {row['attribute_id']:row for row in selected_rows}
    # Missing optional fields must not silently omit a known fabric either.
    for rule in material_rules:
        aid = str(rule['id'])
        if not attributes.get(aid):
            issue('请核实后填写材质：' + result['suggestion'])
        if can_complete and (not attributes.get(aid) or (aid in selected and not compatible(rows + [selected[aid]]))):
            result['automatic_attributes'][aid] = result['suggested_attributes'][aid]
    if result['issues']:
        result.update(status='conflict', message='面料尚未核对一致，请处理下方问题后重新检查。')
    else:
        result.update(status='consistent' if active else 'unknown',
                      message='当前文案与上架材质一致；实际成分仍以实物标签或供应商资料为准。' if active else
                              '只有采集属性提供了面料，当前文案没有可交叉核对的成分；请依据实物标签或供应商资料确认。')
    return result
