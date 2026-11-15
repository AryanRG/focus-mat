"""squeeze(css) -> css: a shorter theme stylesheet that renders the same.

Last build step for the Focus Mat theme. Pure: no files, no network, no randomness.

Steps (each one keeps every cascade winner and every computed colour):
  parse        comments dropped, whitespace outside strings and url() collapsed
  values       linear-gradient(180deg|to bottom, ...) -> linear-gradient(...)
               transparent -> #0000, #aabbcc -> #abc, rgba(r,g,b,a) -> #rrggbbaa when a*255 is
               a whole number (the same colour), 0.5 -> .5, 1.50 -> 1.5, font:400 14px -> font:14px,
               url("data:...base64...") -> url(data:...) when nothing inside needs quotes.
               Colour rewrites only in colour-carrying properties, never in custom properties
               the app reads.
  selectors    [a="ident"] -> [a=ident], no spaces around > + ~ and after commas
  keyframes    100% -> to
  names        the theme's own names (--fm-*, @keyframes fm-*) -> one letter names that the
               app does not use (checked against every --name in the app bundle)
  rules        adjacent rules with the same body merge into one selector list;
               runs of adjacent rules that share a selector prefix P (no pseudo-element in P)
               become P{...&::before{}...} (nesting; & is exactly P, so specificity and
               order stay the same);
               declarations repeated in adjacent sibling rules move into one rule in front
               of them, only when no remaining declaration of those rules touches the same
               property family.
  variables    values repeated often enough to pay for themselves become var(--x) defined on
               html[data-theme-id]. Never for values holding url(), attr(), env(), a CSS-wide
               keyword or a var() of a property that is not a root theme variable; never in
               @keyframes or in rules for pseudo-elements other than ::before/::after.

Cases handled with care:
  - hex escapes (.\\31 0, .\\32 xl\\:grid) own one trailing whitespace: never cut there when nesting, never
    collapsed (the old code turned '.\\31 0 .x' into '.\\31{0 .x{}}' and '.\\31 .x' into a descendant selector);
  - --names that also appear in an at-rule prelude or a selector (@property, @container style(), ...) keep
    their names; quoted strings in selectors (:lang("a , b")) keep their spaces;
  - var() factoring: only theme-owned root variables may be referenced, a block that declares one property
    twice (a fallback pair) is left alone, and only long-supported functions may move into a variable;
  - no crash when merging repeats a data: URI or when the root rule receives nested rules.

Invariants asserted on every call: same data: URIs (same multiset), no backslash in any value,
same set of quoted strings, output parses back to itself, every (selector, property, value)
declaration of the input is still present after expanding nesting and variables, output not longer.
"""
import copy
import re
from collections import Counter, OrderedDict

# one letter names that do not occur as --name anywhere in the app bundle (970 files of
# apps/server/dist/client/assets, checked 15 Nov); keyframes names may reuse them (other namespace)
FREE_NAMES = list('fhjkmqvwyBCDGIJKLNOPRWXYZ')
THEME_PREFIX = '--fm-'
KF_PREFIX = 'fm-'

# ------------------------------------------------------------------ scanning helpers

def _skip_string(s, i):
    q = s[i]; i += 1
    while i < len(s):
        c = s[i]
        if c == '\\':
            i += 2; continue
        if c == q:
            return i + 1
        if c == '\n':
            raise ValueError('newline in string')
        i += 1
    raise ValueError('unterminated string')


_URL_RE = re.compile(r'url\(', re.I)


def _skip_url(s, i):
    """i at 'u' of url( ; returns index after the closing paren."""
    j = i + 4
    while j < len(s) and s[j] in ' \t\n\r\f':
        j += 1
    if j < len(s) and s[j] in '"\'':
        j = _skip_string(s, j)
        while j < len(s) and s[j] in ' \t\n\r\f':
            j += 1
        if s[j] != ')':
            raise ValueError('bad url()')
        return j + 1
    while j < len(s) and s[j] != ')':
        if s[j] == '\\':
            j += 1
        j += 1
    return j + 1


def _is_url_at(s, i):
    return s.startswith('url(', i) or s.startswith('URL(', i) and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] in '-_'))


def strip_comments(s):
    out = []; i = 0
    while i < len(s):
        c = s[i]
        if c in '"\'':
            j = _skip_string(s, i); out.append(s[i:j]); i = j
        elif c == '\\':
            out.append(s[i:i + 2]); i += 2
        elif s.startswith('url(', i) and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] in '-_')):
            j = _skip_url(s, i); out.append(s[i:j]); i = j
        elif s.startswith('/*', i):
            j = s.find('*/', i + 2)
            if j < 0:
                raise ValueError('unterminated comment')
            out.append(' '); i = j + 2
        else:
            out.append(c); i += 1
    return ''.join(out)


def _scan(s, i, stops):
    """Index of the first char in stops at bracket depth 0, skipping strings, escapes and url()."""
    depth = 0
    while i < len(s):
        c = s[i]
        if c in '"\'':
            i = _skip_string(s, i); continue
        if c == '\\':
            i += 2; continue
        if c in 'uU' and s[i:i + 4].lower() == 'url(' and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] in '-_')):
            i = _skip_url(s, i); continue
        if depth == 0 and c in stops:
            return i
        if c in '([':
            depth += 1
        elif c in ')]':
            depth -= 1
        i += 1
    return i


def split_top(s, sep=','):
    """Split at sep outside brackets/strings/url()."""
    parts = []; i = 0; start = 0
    while True:
        j = _scan(s, i, sep)
        parts.append(s[start:j])
        if j >= len(s):
            return parts
        start = i = j + 1

# ------------------------------------------------------------------ tree

class Decl:
    __slots__ = ('prop', 'value', 'imp')
    def __init__(self, prop, value, imp):
        self.prop, self.value, self.imp = prop, value, imp
    def text(self):
        return self.prop + ':' + self.value + ('!important' if self.imp else '')
    def key(self):
        return (self.prop, self.value, self.imp)


class Rule:
    __slots__ = ('sel', 'items')
    def __init__(self, sel, items):
        self.sel, self.items = sel, items


class At:
    __slots__ = ('name', 'prelude', 'items')
    def __init__(self, name, prelude, items):
        self.name, self.prelude, self.items = name, prelude, items  # items None for statements


_WS = re.compile(r'\s+')
_IMP = re.compile(r'\s*!\s*important\s*$', re.I)
DECL_BLOCK_AT = {'font-face', 'property', 'page', 'counter-style', 'font-palette-values', 'view-transition', 'position-try'}


_HEXD = set('0123456789abcdefABCDEF')


def _escape_end(s, i):
    """s[i] == '\\'. Index after the whole escape: a hex escape takes up to 6 hex digits plus ONE optional
    whitespace character that belongs to the escape (so '.\\31 .x' is the compound '.1.x', not a descendant)."""
    j = i + 1
    if j < len(s) and s[j] in _HEXD:
        k = j
        while k < len(s) and k - j < 6 and s[k] in _HEXD:
            k += 1
        if k < len(s) and s[k] in ' \t\n\f':
            k += 1
        elif s.startswith('\r\n', k):
            k += 2
        elif k < len(s) and s[k] == '\r':
            k += 1
        return k
    return min(len(s), i + 2)


def _escape_cuts(s):
    """Set of cut positions k (between s[k-1] and s[k]) that fall inside an escape sequence."""
    bad = set(); i = 0
    while i < len(s):
        c = s[i]
        if c in '"\'':
            try:
                j = _skip_string(s, i)
            except ValueError:
                return bad | set(range(i + 1, len(s) + 1))
            bad.update(range(i + 1, j)); i = j; continue
        if c == '\\':
            j = _escape_end(s, i); bad.update(range(i + 1, j)); i = j; continue
        i += 1
    return bad


def _norm_escape(e):
    """An escape token with its terminating whitespace normalised to one space."""
    if len(e) > 1 and e[1] in _HEXD and e[-1] in ' \t\n\r\f':
        return e.rstrip(' \t\n\r\f') + ' '
    return e


def _collapse_ws(t):
    """Collapse whitespace outside strings and url(). Escapes are copied whole (with the one whitespace
    character that ends a hex escape), so whitespace after a hex escape keeps its meaning."""
    out = []; i = 0; t = t.strip()
    while i < len(t):
        c = t[i]
        if c in '"\'':
            j = _skip_string(t, i); out.append(t[i:j]); i = j
        elif c == '\\':
            j = _escape_end(t, i); out.append(_norm_escape(t[i:j])); i = j
        elif t[i:i + 4].lower() == 'url(' and (i == 0 or not (t[i - 1].isalnum() or t[i - 1] in '-_')):
            j = _skip_url(t, i); out.append(t[i:j]); i = j
        elif c in ' \t\n\r\f':
            j = i
            while j < len(t) and t[j] in ' \t\n\r\f':
                j += 1
            out.append(' '); i = j
        else:
            out.append(c); i += 1
    return ''.join(out)


def _parse_decl(text):
    text = text.strip()
    k = text.index(':')
    prop = text[:k].strip()
    val = text[k + 1:]
    imp = False
    m = _IMP.search(val)
    if m and _scan(val, 0, '!') == m.start() + val[m.start():].index('!'):
        imp = True; val = val[:m.start()]
    elif '!' in mask(val)[0]:
        raise ValueError('unexpected ! in value: ' + text[:60])
    if prop.startswith('--'):
        val = val.strip()           # custom property values: keep inner text exactly
    else:
        val = _collapse_ws(val)
    return Decl(prop, val, imp)


def _parse_block(s, i, kind):
    """kind: 'top' (rules only), 'style' (decls and nested rules), 'decls', 'keyframes'."""
    items = []
    while True:
        while i < len(s) and s[i] in ' \t\n\r\f;':
            i += 1
        if i >= len(s):
            if kind != 'top':
                raise ValueError('unexpected end of css')
            return items, i
        if s[i] == '}':
            if kind == 'top':
                raise ValueError('stray }')
            return items, i + 1
        if s[i] == '@':
            m = re.match(r'@([\w-]+)', s[i:])
            name = m.group(1)
            j = _scan(s, i + len(m.group(0)), '{;}')
            prelude = s[i + len(m.group(0)):j]
            if j >= len(s) or s[j] in ';}':
                items.append(At(name, _collapse_ws(prelude), None))
                i = j + (1 if j < len(s) and s[j] == ';' else 0)
                continue
            low = name.lower()
            if low.endswith('keyframes'):
                sub, i = _parse_block(s, j + 1, 'keyframes')
            elif low in DECL_BLOCK_AT:
                sub, i = _parse_block(s, j + 1, 'decls')
            else:
                sub, i = _parse_block(s, j + 1, 'style' if kind == 'style' else 'group')
            items.append(At(name, _collapse_ws(prelude), sub))
            continue
        j = _scan(s, i, '{;}')
        if j < len(s) and s[j] == '{' and kind != 'decls':
            sel = s[i:j]
            sub, i = _parse_block(s, j + 1, 'decls' if kind == 'keyframes' else 'style')
            items.append(Rule(_collapse_ws(sel), sub))
        else:
            if kind in ('top', 'group'):
                raise ValueError('declaration at top level: ' + s[i:j][:60])
            items.append(_parse_decl(s[i:j]))
            i = j + 1 if j < len(s) and s[j] == ';' else j
    return items, i


def parse(css):
    items, _ = _parse_block(strip_comments(css), 0, 'top')
    return items

# ------------------------------------------------------------------ serialisation

def _ser_items(items):
    out = []
    for k, it in enumerate(items):
        if isinstance(it, Decl):
            out.append(it.text())
            nxt = items[k + 1] if k + 1 < len(items) else None
            if nxt is not None:
                out.append(';')
        elif isinstance(it, Rule):
            out.append(it.sel + '{' + _ser_items(it.items) + '}')
        else:
            out.append(_ser_at(it))
    return ''.join(out)


def _ser_at(a):
    p = a.prelude
    head = '@' + a.name + ('' if p.startswith('(') or not p else ' ') + p
    if a.items is None:
        return head + ';'
    return head + '{' + _ser_items(a.items) + '}'


def serialize(items):
    return _ser_items(items)

# ------------------------------------------------------------------ masking (strings, url())

def mask(v):
    """Replace strings and url(...) with \x00n\x01 placeholders."""
    parts = []; out = []; i = 0
    while i < len(v):
        c = v[i]
        if c in '"\'':
            j = _skip_string(v, i)
        elif v[i:i + 4].lower() == 'url(' and (i == 0 or not (v[i - 1].isalnum() or v[i - 1] in '-_')):
            j = _skip_url(v, i)
        else:
            out.append(c); i += 1; continue
        out.append('\x00%d\x01' % len(parts)); parts.append(v[i:j]); i = j
    return ''.join(out), parts


def unmask(m, parts):
    return re.sub('\x00(\\d+)\x01', lambda g: parts[int(g.group(1))], m)

# ------------------------------------------------------------------ value rewrites

COLOR_PROPS = re.compile(r'^(?:-webkit-)?(?:background(?:-color|-image)?|color|border(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?(?:-color)?|outline(?:-color)?|box-shadow|text-shadow|fill|stroke|caret-color|accent-color|column-rule(?:-color)?|text-decoration(?:-color)?|text-emphasis-color|scrollbar-color|stop-color|flood-color|lighting-color)$')
_NUM = re.compile(r'(?<![\w.#\x00-])(-?)(\d*)\.(\d+)(?![\d.])(?!e[+-]?\d)')
_HEX = re.compile(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![\w-])')
_RGBA = re.compile(r'(?<![\w-])rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(\d*\.?\d+)(%?)\s*)?\)', re.I)
_TRANSP = re.compile(r'(?<![\w-])transparent(?![\w(-])', re.I)
_LG180 = re.compile(r'(?<![\w-])((?:repeating-)?linear-gradient\()\s*(?:180deg|to bottom)\s*,', re.I)
_UNQ_OK = re.compile(r'^[A-Za-z0-9+/=;:,._-]+$')


def _num(m):
    sign, ip, fp = m.group(1), m.group(2), m.group(3).rstrip('0')
    ip = ip.lstrip('0')
    if not fp:
        r = (ip or '0')
        if r == '0':
            sign = ''
        return sign + r
    return sign + ip + '.' + fp


def _hex_short(h):
    h = h.lower()
    if all(h[k] == h[k + 1] for k in range(0, len(h), 2)):
        return h[::2]
    return h


def _rgba(m):
    r, g, b = (int(m.group(k)) for k in (1, 2, 3))
    if max(r, g, b) > 255:
        return m.group(0)
    a = m.group(4)
    if a is None:
        alpha = 255
    else:
        x = float(a) / (100.0 if m.group(5) else 1.0)
        if x >= 1:
            alpha = 255
        else:
            y = x * 255
            if abs(y - round(y)) > 1e-9:
                return m.group(0)
            alpha = int(round(y))
    h = '%02x%02x%02x' % (r, g, b) + ('' if alpha == 255 else '%02x' % alpha)
    h = '#' + _hex_short(h)
    return h if len(h) < len(m.group(0)) else m.group(0)


def _url_unquote(u):
    m = re.match(r'url\(\s*(["\'])(data:[^"\']*)\1\s*\)$', u, re.I)
    if m and _UNQ_OK.match(m.group(2)):
        return 'url(' + m.group(2) + ')'
    return u


def rewrite_value(prop, value, app_custom):
    """app_custom: True for custom properties the app may read (left exactly as written)."""
    if app_custom:
        return value
    m, parts = mask(value)
    is_custom = prop.startswith('--')
    parts = [_url_unquote(p) if p[:4].lower() == 'url(' else p for p in parts]
    m = _LG180.sub(r'\1', m)
    if not is_custom:
        m = _NUM.sub(_num, m)
        if COLOR_PROPS.match(prop.lower()):
            m = _TRANSP.sub('#0000', m)
            m = _RGBA.sub(_rgba, m)
            m = _HEX.sub(lambda g: '#' + _hex_short(g.group(1)), m)
        if prop.lower() == 'font':
            m = re.sub(r'^(?:400|normal)\s+(?=(?:\d|\.\d|(?:clamp|calc|min|max)\())', '', m)
    return unmask(m, parts)

# ------------------------------------------------------------------ selector rewrites

_IDENT = re.compile(r'^-?(?:[_a-zA-Z]|--)[_a-zA-Z0-9-]*$')


def rewrite_selector(sel):
    out = []; i = 0
    while i < len(sel):
        c = sel[i]
        if c == '\\':
            out.append(sel[i:i + 2]); i += 2; continue
        if c == '[':
            j = _scan(sel, i + 1, ']')
            inner = sel[i + 1:j]
            m = re.match(r'^\s*([\w-]+)\s*([~|^$*]?=)\s*(["\'])(.*)\3\s*([iIsS])?\s*$', inner, re.S)
            if m and '\\' not in m.group(4) and _IDENT.match(m.group(4)):
                inner = m.group(1) + m.group(2) + m.group(4) + (' ' + m.group(5) if m.group(5) else '')
            elif m:
                inner = m.group(1) + m.group(2) + m.group(3) + m.group(4) + m.group(3) + (' ' + m.group(5) if m.group(5) else '')
            out.append('[' + inner + ']'); i = j + 1; continue
        if c in '"\'':
            j = _skip_string(sel, i); out.append(sel[i:j]); i = j; continue
        out.append(c); i += 1
    s = ''.join(out)
    # spaces around combinators and commas, and inside parens edges (outside [], strings and escapes)
    res = []; i = 0
    while i < len(s):
        c = s[i]
        if c == '\\':
            j = _escape_end(s, i); res.append(s[i:j]); i = j; continue
        if c in '"\'':
            j = _skip_string(s, i); res.append(s[i:j]); i = j; continue
        if c == '[':
            j = _scan(s, i + 1, ']'); res.append(s[i:j + 1]); i = j + 1; continue
        if c == ' ':
            nxt = s[i + 1] if i + 1 < len(s) else ''
            prv = res[-1][-1] if res and res[-1] else ''
            if nxt in '>+~,)' or prv in '>+~,(' or nxt == '' or prv == '':
                i += 1; continue
        res.append(c); i += 1
    return ''.join(res)

# ------------------------------------------------------------------ walking

def walk_decls(items, ctx=()):
    """Yield (decl, owner_items, index, ctx) for every declaration; ctx is the chain of selectors / at-rules."""
    for k, it in enumerate(items):
        if isinstance(it, Decl):
            yield it, items, k, ctx
        elif isinstance(it, Rule):
            yield from walk_decls(it.items, ctx + (('rule', it.sel),))
        elif it.items is not None:
            yield from walk_decls(it.items, ctx + (('at', it.name.lower(), it.prelude),))


def walk_rules(items, ctx=()):
    for it in items:
        if isinstance(it, Rule):
            yield it, ctx
            yield from walk_rules(it.items, ctx + (('rule', it.sel),))
        elif isinstance(it, At) and it.items is not None:
            yield from walk_rules(it.items, ctx + (('at', it.name.lower(), it.prelude),))


def expand_selectors(ctx):
    """Flattened selector list for a context chain (nesting only ever uses a leading &)."""
    sels = ['']
    for c in ctx:
        if c[0] != 'rule':
            continue
        new = []
        for parent in sels:
            for member in split_top(c[1]):
                member = member.strip()
                if not parent:
                    new.append(member)
                elif member.startswith('&'):
                    new.append(parent + member[1:])
                elif member[:1] in '>+~':
                    new.append(parent + member)
                else:
                    assert '&' not in _strip_brackets(member), member
                    new.append(parent + ' ' + member)
        sels = new
    return sels


def _strip_brackets(s):
    return re.sub(r'\[[^\]]*\]|"[^"]*"|\'[^\']*\'', '', s)


def in_keyframes(ctx):
    return any(c[0] == 'at' and c[1].endswith('keyframes') for c in ctx)

# ------------------------------------------------------------------ property families

_MULTI = {
    'font': {'line-height'}, 'inset': {'top', 'right', 'bottom', 'left'},
    'gap': {'row-gap', 'column-gap'}, 'white-space': {'text-wrap'},
    'place-items': {'align-items', 'justify-items'}, 'place-content': {'align-content', 'justify-content'},
    'place-self': {'align-self', 'justify-self'}, 'columns': {'column'}, 'border': {'outline'},
}


def _fam(p):
    p = p.lower()
    if p.startswith('-webkit-'):
        p = p[8:]
    return p


_PHYS = {'top', 'right', 'bottom', 'left'}
_ALIAS = {'grid-gap': 'gap', 'grid-row-gap': 'row-gap', 'grid-column-gap': 'column-gap', 'word-wrap': 'overflow-wrap',
          'page-break-before': 'break-before', 'page-break-after': 'break-after', 'page-break-inside': 'break-inside',
          'alignment-baseline': 'vertical-align', 'baseline-shift': 'vertical-align', 'baseline-source': 'vertical-align'}


def _size_axis(p):
    q = re.sub(r'^(?:min|max)-', '', p)
    return q if q in ('width', 'height', 'inline-size', 'block-size') else None


def overlap(a, b):
    a, b = _fam(a), _fam(b)
    a, b = _ALIAS.get(a, a), _ALIAS.get(b, b)
    if a in _PHYS and b in _PHYS:
        return a == b
    if (a in _PHYS and b.startswith('inset')) or (b in _PHYS and a.startswith('inset')):
        return True
    sa, sb = _size_axis(a), _size_axis(b)
    if sa and sb:
        pa, pb = a[:4] if a[:4] in ('min-', 'max-') else '', b[:4] if b[:4] in ('min-', 'max-') else ''
        if pa != pb:
            return False
        if sa == sb:
            return True
        return (sa in ('width', 'height')) != (sb in ('width', 'height'))   # physical vs logical
    if a == 'all' or b == 'all':
        return not (a.startswith('--') or b.startswith('--'))
    if a.startswith('--') or b.startswith('--'):
        return a == b
    if a == b or a.split('-')[0] == b.split('-')[0]:
        return True
    for x, y in ((a, b), (b, a)):
        for sh, longs in _MULTI.items():
            if x == sh or x.startswith(sh + '-'):
                if any(y == l or y.startswith(l + '-') or y.split('-')[0] == l for l in longs):
                    return True
    return False

# ------------------------------------------------------------------ rule passes

def _is_pseudo_element_free(p):
    q = _strip_brackets(p)
    return '::' not in q and not re.search(r':(?:before|after|first-line|first-letter)\b', q, re.I)


# pseudo-classes and pseudo-elements Chromium 152 is known to parse. Selectors using anything else are
# never joined into a new selector list (one unknown member would drop the whole list).
KNOWN_PSEUDO = set('''hover focus focus-visible focus-within active visited link any-link is where not has
first-child last-child only-child nth-child nth-last-child first-of-type last-of-type only-of-type nth-of-type
nth-last-of-type empty root checked disabled enabled indeterminate placeholder-shown read-only read-write required
optional valid invalid default target scope defined autofill open popover-open modal fullscreen dir lang host
before after selection placeholder marker backdrop first-line first-letter highlight file-selector-button
-webkit-scrollbar -webkit-scrollbar-thumb -webkit-scrollbar-track -webkit-scrollbar-corner -webkit-autofill'''.split())


def _list_safe(sel):
    for m in re.finditer(r'::?(-?[A-Za-z][\w-]*)', _strip_brackets(sel)):
        if m.group(1).lower() not in KNOWN_PSEUDO:
            return False
    return True


def merge_adjacent(items):
    """A{X}B{X} -> A,B{X} for adjacent style rules with the same body (any depth, not in keyframes)."""
    out = []
    for it in items:
        if isinstance(it, Rule):
            it.items = merge_adjacent(it.items)
        elif isinstance(it, At) and it.items is not None and not it.name.lower().endswith('keyframes'):
            it.items = merge_adjacent(it.items)
        if (out and _flat_rule(it) and _flat_rule(out[-1]) and it.items and _list_safe(it.sel) and _list_safe(out[-1].sel)
                and _ser_items(it.items) == _ser_items(out[-1].items)):
            members = [m for m in split_top(out[-1].sel)]
            for m in split_top(it.sel):
                if m not in members:
                    members.append(m)
            out[-1].sel = ','.join(members)
            continue
        out.append(it)
    return out


_BOUND = set(' >+~.[:#')


def _norm_member(m, nested):
    m = m.strip()
    if not nested or m.startswith('&'):
        return m
    if m[:1] in '>+~':
        return '&' + m
    return '& ' + m


def _common_prefix(members, nested=False):
    members = [_norm_member(m, nested) for m in members]
    p = members[0]
    for m in members[1:]:
        k = 0
        while k < len(p) and k < len(m) and p[k] == m[k]:
            k += 1
        p = p[:k]
        if not p:
            return ''
    # cut positions inside an escape (hex escapes own one trailing whitespace) or a string are never boundaries
    cuts = [_escape_cuts(m) for m in members]
    # shrink to a valid boundary
    while p:
        q = p.rstrip(' ')
        ok = (q and q[-1] not in '>+~,(:.#[|&' and not q.endswith('\\') and _balanced(q) and _is_pseudo_element_free(q)
              and not any(len(q) in c for c in cuts)
              and ('&' not in _strip_brackets(q) if not nested else
                   (q.startswith('&') and len(q) > 1 and '&' not in _strip_brackets(q[1:])))
              and all((len(m) == len(q)) or (m[len(q)] in _BOUND) for m in members))
        if ok:
            # a descendant remainder must start with a real compound after the space
            return q
        p = p[:-1]
    return ''


def _balanced(q):
    d = 0; i = 0
    while i < len(q):
        c = q[i]
        if c == '\\':
            i += 2; continue
        if c in '"\'':
            try:
                i = _skip_string(q, i); continue
            except ValueError:
                return False
        if c in '([':
            d += 1
        elif c in ')]':
            d -= 1
            if d < 0:
                return False
        i += 1
    return d == 0


def _nested_member(rem):
    if rem == '':
        return '&'
    if rem[0] == ' ':
        r = rem.lstrip(' ')
        return r            # relative selector, implicit "& "
    if rem[0] in '>+~':
        return rem
    return '&' + rem


def _nest_run(rules, P, nested=False):
    """Build the nested rule for consecutive rules that all share prefix P."""
    rules = copy.deepcopy(rules)
    children = []
    first = rules[0]
    start = 0
    parent_items = []
    if [_norm_member(m, nested) for m in split_top(first.sel)] == [P]:
        parent_items = list(first.items); start = 1
    for r in rules[start:]:
        sel = ','.join(_nested_member(_norm_member(m, nested)[len(P):]) for m in split_top(r.sel))
        children.append(Rule(sel, r.items))
    return Rule(P, parent_items + children)


def nest_prefixes(items, nested=False):
    """DP over each list of sibling rules (top level, inside @media/@supports, and inside style rules)."""
    for it in items:
        if isinstance(it, At) and it.items is not None and not it.name.lower().endswith('keyframes') and it.name.lower() not in DECL_BLOCK_AT:
            it.items = nest_prefixes(it.items, nested)
    n = len(items)
    best = [0] * (n + 1)      # best saving for items[i:]
    choice = [None] * (n + 1)
    for i in range(n - 1, -1, -1):
        best[i] = best[i + 1]; choice[i] = None
        if not isinstance(items[i], Rule):
            continue
        members = []
        orig = 0
        for j in range(i, n):
            r = items[j]
            if not isinstance(r, Rule):
                break
            members += split_top(r.sel)
            orig += len(_ser_items([r]))
            P = _common_prefix(members, nested)
            if not P:
                break
            if j == i and len(members) == 1:
                continue
            built = _nest_run(items[i:j + 1], P, nested)
            built.items = nest_prefixes(built.items, True)
            save = orig - len(_ser_items([copy.deepcopy(built)]))
            if save > 0 and save + best[j + 1] > best[i]:
                best[i] = save + best[j + 1]; choice[i] = (j, P)
    out = []; i = 0
    while i < n:
        if choice[i]:
            j, P = choice[i]
            out.append(_nest_run(items[i:j + 1], P, nested)); i = j + 1
        else:
            out.append(items[i]); i += 1
    for it in out:
        if isinstance(it, Rule):
            it.items = nest_prefixes(it.items, True)
    return out


def _flat_rule(r):
    return isinstance(r, Rule) and all(isinstance(x, Decl) for x in r.items)


def extract_common(items):
    """Adjacent flat sibling rules sharing declarations: S1,..,Sk{common}S1{rest}.. (safe by property families)."""
    for it in items:
        if isinstance(it, Rule):
            it.items = extract_common(it.items)
        elif isinstance(it, At) and it.items is not None and not it.name.lower().endswith('keyframes') and it.name.lower() not in DECL_BLOCK_AT:
            it.items = extract_common(it.items)
    n = len(items)
    best = [0] * (n + 1); choice = [None] * (n + 1)
    for i in range(n - 1, -1, -1):
        best[i] = best[i + 1]
        if not _flat_rule(items[i]):
            continue
        for j in range(i + 1, n):
            if not _flat_rule(items[j]):
                break
            run = items[i:j + 1]
            plan = _common_plan(run)
            if plan is None:
                continue
            new = _apply_common(run, plan)
            save = len(_ser_items(run)) - len(_ser_items(new))
            if save > 0 and save + best[j + 1] > best[i]:
                best[i] = save + best[j + 1]; choice[i] = (j, plan)
    out = []; i = 0
    while i < n:
        if choice[i]:
            j, plan = choice[i]
            out += _apply_common(items[i:j + 1], plan); i = j + 1
        else:
            out.append(items[i]); i += 1
    return out


def _common_plan(run):
    for r in run:
        props = [d.prop.lower() for d in r.items]
        if len(set(props)) != len(props):
            return None
    keys = [set(d.key() for d in r.items) for r in run]
    common = [d for d in run[0].items if all(d.key() in k for k in keys)]
    while True:
        ck = set(d.key() for d in common)
        rest = [d for r in run for d in r.items if d.key() not in ck]
        keep = [c for c in common if not any(overlap(d.prop, c.prop) for d in rest)]
        if len(keep) == len(common):
            break
        common = keep
    if not common or not all(_list_safe(r.sel) for r in run):
        return None
    # common declarations that overlap each other must keep one order in every rule
    for r in run:
        order = [d.key() for d in r.items if d.key() in ck]
        for a in range(len(common)):
            for b in range(a + 1, len(common)):
                if overlap(common[a].prop, common[b].prop) and order.index(common[a].key()) > order.index(common[b].key()):
                    return None
    return common


def _apply_common(run, common):
    ck = set(d.key() for d in common)
    members = []
    for r in run:
        for m in split_top(r.sel):
            if m not in members:
                members.append(m)
    out = [Rule(','.join(members), list(common))]
    for r in run:
        rest = [d for d in r.items if d.key() not in ck]
        if rest:
            out.append(Rule(r.sel, rest))
    return out

# ------------------------------------------------------------------ reordering

def _props(it):
    if isinstance(it, Decl):
        return {it.prop.lower()}
    if isinstance(it, Rule):
        out = set()
        for x in it.items:
            out |= _props(x)
        return out
    if it.name.lower().endswith('keyframes'):
        return set()
    if it.items is None or it.name.lower() in DECL_BLOCK_AT or it.name.lower() in ('layer', 'import', 'namespace', 'charset'):
        return None          # barrier
    out = set()
    for x in it.items:
        p = _props(x)
        if p is None:
            return None
        out |= p
    return out


def commute(a, b):
    """True when swapping adjacent items a and b cannot change any cascade winner:
    no property of one touches a property of the other (shorthands, longhands, logical twins)."""
    pa, pb = _props(a), _props(b)
    if pa is None or pb is None:
        return False
    return not any(overlap(x, y) for x in pa for y in pb)


def _structure(tree, nest, extract, merge_first):
    t = copy.deepcopy(tree)
    if merge_first:
        t = merge_adjacent(t)
    if nest:
        t = nest_prefixes(t)
    if extract:
        t = extract_common(t)
    t = merge_adjacent(t)
    return t


def _members_of(it):
    return split_top(it.sel) if isinstance(it, Rule) else []


def best_structure(tree, nest, extract):
    def build(t):
        a = _structure(t, nest, extract, True)
        b = _structure(t, nest, extract, False)
        la, lb = len(serialize(a)), len(serialize(b))
        return (a, la) if la <= lb else (b, lb)
    best_t, best_len = build(tree)
    cur = tree
    if not nest:
        return best_t
    for _ in range(30):
        improved = None
        n = len(cur)
        for i in range(n):
            if not isinstance(cur[i], Rule):
                continue
            for j in range(max(0, i - 12), n if False else min(n, i + 13)):
                if j == i or not isinstance(cur[j], Rule):
                    continue
                if not _common_prefix(_members_of(cur[i]) + _members_of(cur[j])):
                    continue
                # move cur[i] next to cur[j] (right after j when j<i, right before j when j>i)
                lo, hi = (j + 1, i) if j < i else (i + 1, j)
                if lo >= hi and j < i:
                    continue
                if not all(commute(cur[i], cur[k]) for k in range(lo, hi)):
                    continue
                t = list(cur)
                x = t.pop(i)
                t.insert(j + 1 if j < i else j - 1, x)
                bt, bl = build(t)
                if bl < best_len and (improved is None or bl < improved[1]):
                    improved = (t, bl, bt)
        if improved is None:
            break
        cur, best_len, best_t = improved
    return best_t


# ------------------------------------------------------------------ names

def rename_theme_names(items, taken):
    """--fm-* custom properties / anchor names and @keyframes fm-* get one letter names."""
    css_text = serialize(items)
    dashed = Counter()
    for d, _, _, _ in walk_decls(items):
        if d.prop.startswith(THEME_PREFIX):
            dashed[d.prop] += 1
        mv, _ = mask(d.value)
        for nm in re.findall(r'(?<![\w-])(--fm-[\w-]+)', mv):
            dashed[nm] += 1
    kf = Counter()
    def kf_walk(its):
        for it in its:
            if isinstance(it, At):
                if it.name.lower().endswith('keyframes') and it.prelude.startswith(KF_PREFIX):
                    kf[it.prelude] += 1
                if it.items is not None:
                    kf_walk(it.items)
            elif isinstance(it, Rule):
                kf_walk(it.items)
    kf_walk(items)
    for d, _, _, _ in walk_decls(items):
        if d.prop.lower() in ('animation', 'animation-name'):
            for nm in re.findall(r'(?<![\w-])(fm-[\w-]+)', mask(d.value)[0]):
                if nm in kf:
                    kf[nm] += 1
    free = [x for x in FREE_NAMES if '--' + x not in taken]
    # names that also appear in an at-rule prelude (@property --x, @container style(--x:..), @supports (--x:..),
    # @position-try --x, ...) or in a selector are not renamed: the prelude/selector would keep the old name.
    pinned = set()
    def pin_walk(its):
        for it in its:
            if isinstance(it, At):
                pinned.update(re.findall(r'(?<![\w-])(--[\w-]+)', it.prelude))
                if it.items is not None:
                    pin_walk(it.items)
            elif isinstance(it, Rule):
                pinned.update(re.findall(r'(?<![\w-])(--[\w-]+)', it.sel))
                pin_walk(it.items)
    pin_walk(items)
    dmap = {}
    for nm, _ in sorted(dashed.items(), key=lambda kv: (-kv[1] * (len(kv[0]) - 3), kv[0])):
        if len(nm) <= 3 or not free or nm in pinned:
            continue
        dmap[nm] = '--' + free.pop(0)
    kfree = [x for x in FREE_NAMES if not re.search(r'@keyframes ' + x + r'(?![\w-])', css_text)]
    kmap = {}
    for nm, _ in sorted(kf.items(), key=lambda kv: (-kv[1], kv[0])):
        if kfree:
            kmap[nm] = kfree.pop(0)
    def sub_dashed(v):
        m, parts = mask(v)
        m = re.sub(r'(?<![\w-])(--fm-[\w-]+)(?![\w-])', lambda g: dmap.get(g.group(1), g.group(1)), m)
        return unmask(m, parts)
    for d, _, _, _ in walk_decls(items):
        if d.prop in dmap:
            d.prop = dmap[d.prop]
        d.value = sub_dashed(d.value)
        if d.prop.lower() in ('animation', 'animation-name'):
            m, parts = mask(d.value)
            m = re.sub(r'(?<![\w-])(fm-[\w-]+)(?![\w-])', lambda g: kmap.get(g.group(1), g.group(1)), m)
            d.value = unmask(m, parts)
    def kf_ren(its):
        for it in its:
            if isinstance(it, At):
                if it.name.lower().endswith('keyframes') and it.prelude in kmap:
                    it.prelude = kmap[it.prelude]
                if it.items is not None:
                    kf_ren(it.items)
            elif isinstance(it, Rule):
                kf_ren(it.items)
    kf_ren(items)
    return dmap, kmap

# ------------------------------------------------------------------ variables

ROOT_SEL = 'html[data-theme-id]'
_BAD_IN_VAR = re.compile(r'(?<![\w-])(?:attr|env|if|inherit|initial|unset|revert|revert-layer|anchor|anchor-size|calc-size|sibling-index|sibling-count)(?![\w-])', re.I)
_OK_PSEUDO = re.compile(r'::(before|after)\b', re.I)
_OLD_FUNCS = set('''var calc min max clamp rgb rgba hsl hsla hwb lab lch oklab oklch color color-mix linear-gradient
radial-gradient conic-gradient repeating-linear-gradient repeating-radial-gradient repeating-conic-gradient steps
cubic-bezier translate translatex translatey translate3d rotate scale scalex scaley skew skewx skewy matrix blur
saturate brightness contrast drop-shadow grayscale hue-rotate invert opacity sepia minmax repeat fit-content
counter counters'''.split())


def _site_ok(ctx):
    if in_keyframes(ctx):
        return False
    for c in ctx:
        if c[0] == 'at' and c[1] in DECL_BLOCK_AT:
            return False
    for sel in expand_selectors(ctx):
        q = _strip_brackets(sel)
        rest = _OK_PSEUDO.sub('', q)
        if '::' in rest or re.search(r':(?:first-line|first-letter)\b', rest, re.I):
            return False
    return True


def _root_rule(items):
    for it in items:
        if isinstance(it, Rule) and it.sel == ROOT_SEL:
            return it
    r = Rule(ROOT_SEL, [])
    items.insert(0, r)
    return r


def _root_vars(items, root):
    """Custom properties declared on the root rule, theme owned (renamed), declared nowhere else."""
    declared = Counter(d.prop for d, _, _, _ in walk_decls(items) if d.prop.startswith('--'))
    return {d.prop: d.value for d in root.items if isinstance(d, Decl) and d.prop.startswith('--') and declared[d.prop] == 1}


def _candidates(m):
    """Token aligned candidate substrings of a masked value: function calls and comma runs, plus strings."""
    out = set()
    # balanced function calls
    for f in re.finditer(r'(?<![\w-])[a-zA-Z-][\w-]*\(', m):
        j = _scan(m, f.end(), ')')
        if j < len(m):
            out.add(m[f.start():j + 1])
    # comma runs at every paren level
    def runs(seg):
        items = split_top(seg)
        if len(items) > 1:
            for a in range(len(items)):
                for b in range(a + 1, len(items) + 1):
                    if b - a >= 1:
                        out.add(','.join(items[a:b]).strip())
    runs(m)
    for f in re.finditer(r'\(', m):
        j = _scan(m, f.end(), ')')
        runs(m[f.end():j])
    for ph in re.findall('\x00\\d+\x01', m):
        out.add(ph)
    return out


def _occurrences(m, cand):
    """Non-overlapping, token aligned positions of cand in masked value m."""
    res = []; i = 0
    while True:
        k = m.find(cand, i)
        if k < 0:
            return res
        left = m[k - 1] if k > 0 else ''
        right = m[k + len(cand)] if k + len(cand) < len(m) else ''
        if left in ('', '(', ',', ' ', '\x01') and right in ('', ')', ',', ' ', '\x00', '!'):
            # a comma run must not cut a function in half: check balance of cand
            if _balanced(cand):
                res.append(k); i = k + len(cand); continue
        i = k + 1


def _root_insert(root, decl):
    """New root declarations go before any nested rule of the root rule (no declarations after rules)."""
    for k, it in enumerate(root.items):
        if not isinstance(it, Decl):
            root.items.insert(k, decl); return
    root.items.append(decl)


def factor_vars(items, taken, owned=None):
    """owned: custom property names the theme owns and may read through a root variable (renamed --fm-* names
    and names defined here). Any var() of another name stays at the element where it is evaluated."""
    root = _root_rule(items)
    defined_here = set()
    for _ in range(40):
        rv = {k: v for k, v in _root_vars(items, root).items() if owned is None or k in owned or k in defined_here}
        sites = []
        for d, owner, k, ctx in walk_decls(items):
            if d is None or (owner is root.items):
                continue
            if not _site_ok(ctx):
                continue
            # a property declared twice in one block is a fallback pair: with var() the later value would no
            # longer be dropped at parse time when unsupported (Chromium 152), so leave such blocks alone
            if sum(1 for x in owner if isinstance(x, Decl) and _fam(x.prop) == _fam(d.prop)) > 1:
                continue
            mv, parts = mask(d.value)
            sites.append((d, mv, parts))
        stats = {}
        for d, mv, parts in sites:
            for c in _candidates(mv):
                cand_text = unmask(c, parts)
                if not _cand_ok(cand_text, rv):
                    continue
                stats.setdefault(cand_text, 0)
        if not stats:
            break
        best = None
        used_names = set(re.findall(r'--[\w-]+', serialize(items))) | set(taken)
        free = [x for x in FREE_NAMES if '--' + x not in used_names]
        for cand in stats:
            occ = 0
            for d, mv, parts in sites:
                cm = _remask(cand, parts, mv)
                if cm is None:
                    continue
                occ += len(_occurrences(mv, cm))
            existing = [nm for nm, v in rv.items() if v == cand]
            if existing:
                nm = existing[0]
                save = occ * (len(cand) - len('var(%s)' % nm))
            else:
                if not free:
                    continue
                nm = '--' + free[0]
                save = occ * (len(cand) - len('var(%s)' % nm)) - (len(nm) + 1 + len(cand) + 1)
            if occ >= 1 and save > 0 and (best is None or save > best[0] or (save == best[0] and cand < best[1])):
                best = (save, cand, nm, bool(existing))
        if best is None:
            break
        save, cand, nm, exists = best
        for d, mv, parts in sites:
            cm = _remask(cand, parts, mv)
            if cm is None:
                continue
            pos = _occurrences(mv, cm)
            if not pos:
                continue
            for k in reversed(pos):
                mv = mv[:k] + 'var(' + nm + ')' + mv[k + len(cm):]
            d.value = unmask(mv, parts)
        if not exists:
            _root_insert(root, Decl(nm, cand, False)); defined_here.add(nm)
    return defined_here


def _remask(cand, parts, mv):
    """cand as it would appear inside this site's masked value (placeholders for its strings)."""
    m, cparts = mask(cand)
    for idx, p in enumerate(cparts):
        try:
            k = parts.index(p)
        except ValueError:
            return None
        m = m.replace('\x00%d\x01' % idx, '\x00%d\x01' % k, 1)
    return m


def _cand_ok(c, rv):
    if 'url(' in c.lower() or '\\' in c or '!' in c or '\n' in c:
        return False
    if len(c) <= 8:
        return False
    if _BAD_IN_VAR.search(mask(c)[0]):
        return False
    # only functions Chromium has parsed for years: a value the app's Chromium 152 cannot parse would be dropped
    # at parse time when written out (earlier rules still apply), but with var() it turns the property to unset
    for fn in re.findall(r'(?<![\w-])([a-zA-Z-][\w-]*)\(', mask(c)[0]):
        if fn.lower() not in _OLD_FUNCS:
            return False
    for nm in re.findall(r'var\(\s*(--[\w-]+)', c):
        if nm not in rv:
            return False
    if not _balanced(c):
        return False
    return True

# ------------------------------------------------------------------ invariants

def _data_uris(css):
    out = []
    for m in re.finditer(r'data:[^"\')\s]+', css):
        out.append(m.group(0))
    return Counter(out)


def _strings(css):
    """Quoted strings in declaration values, and attribute selector values without their quotes."""
    out = set()
    tree = parse(css)
    for d, _, _, _ in walk_decls(tree):
        out |= set(p for p in mask(d.value)[1] if p[:1] in '"\'')
    for r, ctx in walk_rules(tree):
        if not in_keyframes(ctx):
            for m in re.finditer(r'\[\s*[\w-]+\s*[~|^$*]?=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\]\s]+))', r.sel):
                out.add(('attr', m.group(1) if m.group(1) is not None else m.group(2) if m.group(2) is not None else m.group(3)))
    return out


def _flat(items, var_defs, skip):
    """Multiset of (context, selector, prop, value, imp) with nesting expanded and root vars substituted."""
    res = Counter()
    for d, owner, k, ctx in walk_decls(items):
        ats = tuple(c[1:] for c in ctx if c[0] == 'at')
        if in_keyframes(ctx):
            sels = [ctx[-1][1]] if ctx and ctx[-1][0] == 'rule' else ['']
        else:
            sels = expand_selectors(ctx)
        prop, val = d.prop, d.value
        if True:
            if prop in skip and sels == [ROOT_SEL]:
                continue
            for _ in range(5):
                val = re.sub(r'var\((--[\w-]+)\)', lambda g: var_defs.get(g.group(1), g.group(0)), val)
        for s in sels:
            for member in split_top(s):
                res[(ats, member.strip(), prop, val, d.imp)] += 1
    return res


def squeeze(css, solid_gradients=True, nest=True, extract=True, variables=True, rename=True):
    src = css
    tree = parse(css)
    taken = set(re.findall(r'--[\w-]+', css))
    app_like = lambda p: p.startswith('--') and not p.startswith(THEME_PREFIX)
    # values, selectors, keyframe selectors
    for d, owner, k, ctx in walk_decls(tree):
        d.value = rewrite_value(d.prop, d.value, app_like(d.prop))
        if solid_gradients and COLOR_PROPS.match(d.prop.lower()):
            m, parts = mask(d.value)
            m = re.sub(r'(?<![\w-])linear-gradient\((#[0-9a-f]{3,8}|rgba?\([^()]*\)),\1\)', r'linear-gradient(\1 0 0)', m)
            d.value = unmask(m, parts)
    for r, ctx in walk_rules(tree):
        if in_keyframes(ctx):
            r.sel = ','.join('to' if x.strip() == '100%' else x.strip() for x in split_top(r.sel))
        else:
            r.sel = rewrite_selector(r.sel)
    for it in tree:
        pass
    base_root = next((it for it in tree if isinstance(it, Rule) and it.sel == ROOT_SEL), None)
    base_flat = _flat(tree, _root_vars(tree, base_root) if base_root else {}, set())   # reference after token rewrites
    dmap = kmap = {}
    if rename:
        dmap, kmap = rename_theme_names(tree, taken)
    tree = best_structure(tree, nest, extract)
    new_vars = set()
    if variables:
        new_vars = factor_vars(tree, taken | set(dmap.values()), owned=set(dmap.values()))
    tree = [it for it in tree if not (isinstance(it, Rule) and it.sel == ROOT_SEL and not it.items)]
    out = serialize(tree)

    # ---------------- invariants
    assert len(out) <= len(src), (len(out), len(src))
    # merging two rules with the same body legitimately keeps one copy of a repeated data: URI, so compare sets
    assert set(_data_uris(out)) == set(_data_uris(src)), 'data: URIs changed'
    assert _strings(out) == _strings(src), 'quoted strings changed: %r' % (_strings(src) ^ _strings(out))
    for d, _, _, _ in walk_decls(parse(out)):
        assert '\\' not in d.value, 'backslash in value: ' + d.prop
    assert serialize(parse(out)) == out, 'output does not round-trip'
    # semantic: every declaration of the reference still applies to the same selector
    t2 = parse(out)
    root = next((it for it in t2 if isinstance(it, Rule) and it.sel == ROOT_SEL), None)
    got = _flat(t2, _root_vars(t2, root) if root else {}, new_vars)
    inv_d = {v: k for k, v in dmap.items()}
    inv_k = {v: k for k, v in kmap.items()}
    def back(key):
        ats, sel, prop, val, imp = key
        ats = tuple((a[0], inv_k.get(a[1], a[1])) + tuple(a[2:]) if a[0].endswith('keyframes') else a for a in ats)
        m, parts = mask(val)
        m = re.sub(r'(?<![\w-])(--[\w-]+)(?![\w-])', lambda g: inv_d.get(g.group(1), g.group(1)), m)
        if prop.lower() in ('animation', 'animation-name'):
            m = re.sub(r'(?<![\w-])([A-Za-z][\w-]*)(?![\w-])', lambda g: inv_k.get(g.group(1), g.group(1)), m)
        return (ats, sel, inv_d.get(prop, prop), unmask(m, parts), imp)
    got = Counter({back(k): v for k, v in got.items()})
    # merging a duplicated selector inside a list removes an exact duplicate only
    if set(got) != set(base_flat):
        missing = set(base_flat) - set(got); extra = set(got) - set(base_flat)
        raise AssertionError('declarations changed: missing %r extra %r' % (list(missing)[:3], list(extra)[:3]))
    return out


if __name__ == '__main__':
    import sys
    src = open(sys.argv[1]).read()
    out = squeeze(src)
    print(len(src), '->', len(out), file=sys.stderr)
    if len(sys.argv) > 2:
        open(sys.argv[2], 'w').write(out)
