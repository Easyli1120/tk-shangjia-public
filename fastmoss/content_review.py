"""Detect and remove third-party branding and rights claims before publishing."""
import re
import unicodedata


_RIGHTS_MARKERS = re.compile(
    r"(?:official(?:ly)?\s+authentic|authorized\s+seller|only\s+authorized|"
    r"official\s+store|\b(?:copyright|trademark|replica|counterfeit|"
    r"knock[- ]?off|licensed|genuine)\b|inspired\s+by|1\s*:\s*1|"
    r"侵权|仿冒|高仿|仿牌|授权|官方|正品|[©™®])",
    re.IGNORECASE,
)
_GENERIC_NAMES = {'shop', 'store', 'brand', 'boutique', 'apparel', 'clothing'}
_SENTENCE_END = re.compile(r"[.!?。！？；;\n]+")
_PURCHASE_LANGUAGE = re.compile(
    r"(?:welcome|buy|purchase|shop|order|visit|available\s+(?:at|on)|"
    r"欢迎|购买|下单|进店|店铺|商城)", re.IGNORECASE,
)


def _normal(value):
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", str(value or "").casefold())


def _clean(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip(" -–—|:：")
    normalized = _normal(value)
    if (len(normalized) < 3 and not re.search(r"[\u3400-\u9fff]", normalized)) or normalized in _GENERIC_NAMES:
        return ""
    return value


def source_names(product, source):
    """Return names explicitly present in captured seller or brand fields."""
    values = []
    for value in (
        product.get("source_shop_name"),
        product.get("shop_name"),
        source.get("shop_name"),
        source.get("brand"),
        source.get("brand_name"),
    ):
        cleaned = _clean(value)
        if cleaned:
            values.append((cleaned, "店铺/品牌字段"))
    for value in source.get("brand_names", []):
        cleaned = _clean(value)
        if cleaned:
            values.append((cleaned, "品牌字段"))
    result = []
    seen = set()
    for name, kind in values:
        key = _normal(name)
        if key and key not in seen:
            result.append({"name": name, "kind": kind})
            seen.add(key)
    return result


def find_matches(text, terms):
    text = str(text or "")
    normalized = _normal(text)
    matches = []
    seen = set()
    for term in terms or []:
        name = term.get("name") if isinstance(term, dict) else term
        key = _normal(name)
        if not key or key in seen or key not in normalized:
            continue
        matches.append({"name": str(name), "kind": term.get("kind", "疑似品牌/店铺") if isinstance(term, dict) else "疑似品牌/店铺"})
        seen.add(key)
    return matches


def _has_rights_marker(text):
    return bool(_RIGHTS_MARKERS.search(str(text or "")))


def _has_term(text, terms):
    return bool(find_matches(text, terms))


def _units(text):
    """Split text into sentence-like units while retaining their punctuation."""
    text = str(text or "")
    units = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        units.append(text[start:end])
        start = end
    if start < len(text):
        units.append(text[start:])
    return units


def _remove_terms(text, terms):
    """Remove a matched term from an undelimited title fragment."""
    value = str(text or "")
    for term in terms or []:
        name = term.get("name") if isinstance(term, dict) else term
        normalized = _normal(name)
        if not normalized:
            continue
        # Match the same typography variations as find_matches while keeping
        # punctuation around the rest of a short product title.
        parts = re.split(r"([\s\-‐‑–—]+|['’‘])", str(name))
        pattern = ''.join(
            r"[\s\-‐‑–—]+" if re.fullmatch(r"[\s\-‐‑–—]+", part)
            else r"['’‘]" if part in ("'", "’", "‘")
            else re.escape(part)
            for part in parts
        )
        value = re.sub(r"(?<!\w)" + pattern + r"(?:['’]s\b)?(?!\w)", " ", value, flags=re.I)
    value = re.sub(r"\s+([,，:：])", r"\1", value)
    value = re.sub(r"\(\s*\)|\[\s*\]|【\s*】", " ", value)
    return " ".join(value.split()).strip(" ,，-‐‑–—|:：")


def sanitize_text(text, terms, *, drop_single=True):
    """Drop every sentence containing a flagged term or rights statement.

    A title fragment without sentence punctuation is kept only when removing
    the matched store/brand name leaves useful product wording. This preserves
    compact titles such as ``Brand | Hoodie`` while removing purchase prompts
    and rights claims in their entirety.
    """
    value = str(text or "")
    units = _units(value)
    if not units:
        return ""
    flagged = [_has_term(unit, terms) or _has_rights_marker(unit) for unit in units]
    if not any(flagged):
        return value.strip()
    if len(units) == 1 and not drop_single and not _has_rights_marker(value) and not _PURCHASE_LANGUAGE.search(value):
        return _remove_terms(value, terms)
    kept = [unit for unit, is_flagged in zip(units, flagged) if not is_flagged]
    cleaned = "".join(kept)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n[ \t]*\n+", "\n", cleaned)
    return cleaned.strip(" \t\r\n-‐‑–—|:：")


def sanitize_source(source, product):
    """Return a publishable source projection while retaining raw description."""
    value = dict(source or {})
    raw = value.get("raw_description", value.get("description", ""))
    value["raw_description"] = raw
    value["description"] = sanitize_text(raw, source_names(product or {}, value))
    return value


def review(product, source):
    terms = source_names(product, source)
    title_matches = find_matches(product.get("title"), terms)
    title_markers = sorted(set(match.group(0) for match in _RIGHTS_MARKERS.finditer(product.get("title", ""))))
    description = source.get("raw_description", source.get("description", ""))
    description_matches = find_matches(description, terms)
    markers = sorted(set(match.group(0) for match in _RIGHTS_MARKERS.finditer(description)))
    return {
        "terms": terms,
        "title_matches": title_matches,
        "title_rights_markers": title_markers,
        "description_matches": description_matches,
        "rights_markers": markers,
        "requires_review": bool(title_matches or title_markers or description_matches or markers),
    }


def _ocr_normal(text):
    return _normal(unicodedata.normalize('NFKC', str(text or '')))


def _similar_ocr_name(candidate, name):
    # Only compare whole name candidates. Short/common words need exact matches.
    if len(name) < 6 or not re.fullmatch('[a-z0-9]+', name): return False
    confusable = str.maketrans({'0':'o', '1':'i', 'l':'i', '3':'e', '4':'a', '5':'s', '7':'t', '8':'b', 'm':'n'})
    candidate, name = candidate.translate(confusable), name.translate(confusable)
    limit = 1 if len(name) < 12 else 2
    if abs(len(candidate)-len(name)) > limit: return False
    previous = list(range(len(name)+1))
    for i, char in enumerate(candidate, 1):
        current = [i]
        for j, expected in enumerate(name, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(char!=expected)))
        previous = current
    return previous[-1] <= limit


def ocr_name_matches(image, terms):
    regions = image.get('ocr_regions') or [{'region':'全图','text':image.get('ocr_text','')}]
    matches = []
    for term in terms or []:
        name = term.get('name') if isinstance(term, dict) else term
        key = _ocr_normal(name)
        if not key: continue
        best = None
        for region in sorted(regions, key=lambda r:r.get('region')=='全图'):
            for line in str(region.get('text','')).splitlines():
                words = re.findall(r'[a-z0-9\u3400-\u9fff]+', unicodedata.normalize('NFKC',line).casefold())
                candidates = {_ocr_normal(line)}
                candidates.update(''.join(words[start:start+count]) for start in range(len(words)) for count in range(1,5))
                exact = key in candidates
                if not exact and not any(_similar_ocr_name(candidate,key) for candidate in candidates): continue
                match = {'name':str(name),'kind':term.get('kind','疑似品牌/店铺') if isinstance(term,dict) else '疑似品牌/店铺',
                         'match_type':'exact' if exact else 'variant','region':region.get('region','全图'),'matched_text':line[:200]}
                if best is None or (exact and best['match_type']!='exact'): best = match
        if best: matches.append(best)
    return matches


def image_matches(images, terms):
    result = []
    for image in images:
        item = dict(image)
        matches = ocr_name_matches(item, terms)
        item["text_matches"] = matches
        item["text_detected"] = bool(str(item.get("ocr_text", "")).strip())
        item["review_required"] = bool(matches)
        result.append(item)
    return result
