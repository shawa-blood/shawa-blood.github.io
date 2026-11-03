#!/usr/bin/env python3
"""مزامنة تسجيلات نموذج Google مع «دليل فصائل دم قرية شاوة».

الاستخدام:
  python3 tools/sync.py rows.json

rows.json: مصفوفة صفوف شيت الردود كما هي (الصف الأول عناوين الأعمدة)، مثل ناتج
get_values على النطاق 'ردود النموذج 1'!A1:F2000.

ما يفعله:
  - يقرأ آخر صف تمت معالجته من tools/sync-state.json.
  - يضيف الصفوف الجديدة فقط إلى بيانات index.html (اسم، فصيلة، أزرار اتصال).
  - يتخطى: الفصيلة غير الصحيحة، الاسم القصير، الرقم أو الاسم+الفصيلة الموجودين مسبقًا.
  - لا ينشر تاريخ الميلاد أبدًا، ولا ينشر رقم من عمره أقل من 18 سنة.
  - يحدّث النسخة الثابتة (بدون جافاسكربت) ورقم الإصدار في index.html و service-worker.js.
يطبع ملخصًا بالأعداد فقط (بدون أسماء أو أرقام).
"""
import base64, datetime, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, 'index.html')
SW = os.path.join(ROOT, 'service-worker.js')
STATE = os.path.join(ROOT, 'tools', 'sync-state.json')
GROUPS = ['O+', 'O-', 'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-']
AR_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def norm_phone(raw):
    d = (raw or '').translate(AR_DIGITS).strip()
    intl = d.startswith('+')
    d = re.sub(r'\D', '', d)
    if d.startswith('00'):
        d, intl = d[2:], True
    if not d:
        return None
    if len(d) == 12 and d.startswith('20') and d[2:4] in ('10', '11', '12', '15'):
        return ('+' + d, True)
    if len(d) == 11 and d[:3] in ('010', '011', '012', '015'):
        return ('+2' + d, True)
    if len(d) == 10 and d[:2] in ('10', '11', '12', '15'):
        return ('+20' + d, True)
    if len(d) == 10 and d.startswith('0502'):
        return ('+2' + d, False)  # أرضي المنصورة
    if len(d) == 7 and d.startswith('2'):
        return ('+2050' + d, False)
    if len(d) == 10 and d[:3] in ('053', '054', '055', '056', '057', '058', '059'):
        return ('+966' + d[1:], True)
    if intl and d.startswith('966') and len(d) == 12:
        return ('+' + d, True)
    if intl and d.startswith('965') and len(d) == 11:
        return ('+' + d, True)
    return None


def age(dob):
    s = (dob or '').translate(AR_DIGITS).strip()
    m = re.match(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', s) or None
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', s)
        if not m:
            return None
        mo, d, y = map(int, m.groups())  # تنسيق شيت جوجل الافتراضي M/D/YYYY
    try:
        b = datetime.date(y, mo, d)
    except ValueError:
        return None
    today = datetime.date.today()
    if b.year < 1930 or b > today.replace(year=today.year - 5):
        return None
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))


def norm_name(s):
    s = re.sub(r'[ً-ْـ]', '', s or '')
    s = s.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا').replace('ى', 'ي').replace('ة', 'ه')
    return re.sub(r'\s+', '', s)


def enc(phones):
    s = ';'.join('%s,%d' % (p, w) for p, w in phones)
    return base64.b64encode(s[::-1].encode()).decode()


def dec(ph):
    out = []
    for part in (ph or '').split('~'):
        if not part:
            continue
        try:
            s = base64.b64decode(part).decode()[::-1]
        except Exception:
            continue
        out += [x.split(',')[0] for x in s.split(';') if x]
    return out


def col(header, *keys):
    for i, h in enumerate(header):
        if any(k in h for k in keys):
            return i
    return None


def main(rows_path):
    rows = json.load(open(rows_path, encoding='utf-8'))
    if isinstance(rows, dict):
        rows = rows.get('values', [])
    header, data = rows[0], rows[1:]
    ci = {'name': col(header, 'الإسم', 'الاسم'), 'dob': col(header, 'الميلاد'),
          'p1': col(header, 'رقم الهاتف:'), 'p2': col(header, 'احتياطي'), 'g': col(header, 'فصيلة')}
    if ci['p1'] is None:
        ci['p1'] = col(header, 'الهاتف')
    assert None not in ci.values(), 'أعمدة الشيت غير متوقعة: %r' % header
    state = json.load(open(STATE, encoding='utf-8'))
    last = state['last_row']
    if len(data) < last:
        print('تحذير: عدد الصفوف في الشيت (%d) أقل من آخر صف تمت معالجته (%d). لم يتغير شيء.' % (len(data), last))
        return 2

    html = open(INDEX, encoding='utf-8').read()
    m = re.search(r'(<script type="application/json" id="seedData">)([\s\S]*?)(</script>)', html)
    seed = json.loads(m.group(2))
    recs = seed['records']
    known_phones = {p[-9:] for r in recs for p in dec(r[5] if len(r) > 5 else '')}
    known_ng = {(norm_name(r[1]), r[2]) for r in recs}
    nums = [int(x) for x in re.findall(r'FORM2026-(\d+)', ' '.join(r[0] for r in recs))]
    nxt = max(nums + [0]) + 1

    stats = dict(new_rows=len(data) - last, added=0, with_phone=0, minors=0, skipped_dup=0, skipped_invalid=0)
    def cell(row, k):
        i = ci[k]
        return row[i].strip() if i < len(row) and row[i] else ''
    for row in data[last:]:
        name = re.sub(r'\s+', ' ', cell(row, 'name')).strip(' .،,')
        g = cell(row, 'g').upper().replace(' ', '')
        if g not in GROUPS or len(name.split()) < 2 or re.search(r'\d', name.translate(AR_DIGITS)):
            stats['skipped_invalid'] += 1
            continue
        phones = []
        for k in ('p1', 'p2'):
            n = norm_phone(cell(row, k))
            if n and n[0] not in [p for p, _ in phones]:
                phones.append(n)
        if any(p[-9:] in known_phones for p, _ in phones) or (norm_name(name), g) in known_ng:
            stats['skipped_dup'] += 1
            continue
        a = age(cell(row, 'dob'))
        if a is not None and a < 18:
            phones = []
            stats['minors'] += 1
        rid = 'FORM2026-%03d' % nxt
        nxt += 1
        recs.append([rid, name, g, 'تسجيلات 2026', True, enc(phones) if phones else ''])
        known_ng.add((norm_name(name), g))
        known_phones.update(p[-9:] for p, _ in phones)
        stats['added'] += 1
        stats['with_phone'] += bool(phones)

    state['last_row'] = len(data)
    state['last_sync'] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    if stats['added']:
        version = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).strftime('%Y.%m.%d-%H%M')
        seed['generated'] = version[:10].replace('.', '-')
        payload = json.dumps(seed, ensure_ascii=False).replace('<', '\\u003c')
        html = html[:m.start(2)] + payload + html[m.end(2):]
        html = re.sub(r'\b20\d\d\.\d\d\.\d\d-\d+\b', version, html)
        html = refresh_static(html)
        open(INDEX, 'w', encoding='utf-8').write(html)
        sw = open(SW, encoding='utf-8').read()
        open(SW, 'w', encoding='utf-8').write(re.sub(r'\b20\d\d\.\d\d\.\d\d-\d+\b', version, sw))
        stats['version'] = version
    json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    stats['total_records'] = len(recs)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


def refresh_static(html):
    """يعيد توليد النسخة الثابتة (للمعاينة بدون جافاسكربت) من نفس كود الصفحة."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.route('https://build.local/**', lambda r: r.fulfill(body=html, content_type='text/html'))
        pg.goto('https://build.local/')
        st = pg.evaluate('__shawa.staticParts()')
        b.close()
    html = re.sub(r'(<div class="groups" id="groups"[^>]*>)[\s\S]*?(</div>\s*<button class="btn out btn-all)',
                  lambda mm: mm.group(1) + st['groups'] + mm.group(2), html, count=1)
    html = re.sub(r'(<div class="nojs-only" id="staticList">)[\s\S]*?(</div>\s*</section>)',
                  lambda mm: mm.group(1) + st['list'] + mm.group(2), html, count=1)
    html = re.sub(r'(<div class="stats" id="stats"[^>]*>)[\s\S]*?(</div>\s*<section)',
                  lambda mm: mm.group(1) + st['stats'] + mm.group(2), html, count=1)
    return html


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
