"""Rotating calm quotes for the Focus Mat theme, as one SVG data URI used through content:var(--fm-q).

    quotes_svg(quotes, hold_seconds=120, fade=1.5, width=260) -> (css_url_string, height_px)

quotes is a list of (paragraph1, paragraph2). Everything below was proven in headless Google Chrome
with the picture shown through content:var() on a pseudo-element.

- All quotes sit side by side in ONE xhtml strip inside a <foreignObject>: a multi-column div with one
  260px column per quote. A quote costs its text plus 8 characters:  p1<p/>p2<b/>  . <p/> is the
  paragraph gap (an empty block; its collapsed margins make a 9px gap), <b/> forces a column break.
  Text wraps on its own. Columns are balanced, not fixed height, so a quote that is too long only
  runs off the bottom of its own column (clipped by the picture); it cannot shift the later quotes.
- The strip is moved by <set> elements on x. The cycled count M is factorised (M = r1*r2*...); level
  k holds r_k - 1 sets, each active for one hold in every r_k holds, on its own nested element (the
  foreignObject itself, then nested <svg>s), so the offsets add up to every column in order.
  32 quotes need 5 sets, 30 need 7. The count is never padded: padding repeated the first quotes
  back to back (30 quotes were cycled as 32, so the viewer saw quote 1, 2, 1, 2 in a row). A count
  whose factors need more than MAX_SETS sets (a large prime, e.g. 29 or 31) is refused with the
  nearest counts that work; pass allow_repeat=True to get the old padding instead. A <set> does no
  work while it holds; a held <animate> (even calcMode=discrete) ticks every frame, so none is used.
- Known cosmetic quirk (Chrome 157, measured): the FIRST activation of each set that begins from a
  plain offset (quote 2 at T, and at 4T, 8T, 16T for 32 quotes) lands about 4 frames (67 ms) late,
  so quote 1 shows for those frames at the bottom of the fade (opacity under 0.04, times the 0.7 of
  the usage sites). Later activations come from the id.end chains and are on time.
- One shared <animate> on the root's opacity (1 -> 0 -> 1 over 2*fade s) is centred on every change.
- Heartbeat: an <animate> that begins after more than ~60 s with no SMIL event in the picture starts
  late in Chrome (120 s hold: the <set> switched at 120 s, the fade ran at 176 s). One <set> with no
  visible effect (y on the root <svg>) whose begin/end events come every BEAT s keeps it on time.
- The picture's clock lives as long as some element shows the same URL. If none does, the next
  showing starts again at quote 1: measured after an 8 s gap with one garbage collection, and after
  62 s with no forced collection. KEEPER_CSS keeps one zero-size copy for the life of the page. Its
  price: the picture then runs even when no quote is on screen (about 45 ms of main thread per
  change, every hold, plus the 40 s heartbeat; 0 without the keeper).
"""
import os, re

NS = "xmlns='http://www.w3.org/2000/svg'"
XNS = "xmlns='http://www.w3.org/1999/xhtml'"
FAMILY = 'Iowan Old Style,Palatino Linotype,Palatino,Georgia,serif'
SIZE, LH, GAP = 13.5, 20.5, 9            # px, same as the earlier SVG <text> version
COLOR = '%23d9ceb8'                        # #d9ceb8 with '#' percent-encoded
LINES = 4                                  # lines the slot shows: p1 + p2 together
IDS = 'abcdefgjkmnoqrstuvwxy'              # ids for the sets ('z' is the fade, 'h' the heartbeat)
BEAT = 40                                  # heartbeat period in s; must stay well under ~60 s
FONT_FILE = '/System/Library/Fonts/Supplemental/Iowan Old Style.ttc'   # for the fit check
SAFE_CHARS = 51      # fallback only (no PIL/font): Chrome, 260px, 3,400 samples: ordinary prose took 3 lines
                     # from 78 chars, prose with long words from 72, with capitalised words from 63,
                     # all-caps words from 52. 51 is the only limit that held for every sample.
MAX_SETS = 21        # one id per set (IDS); more sets would also cost ~75 characters each
# The theme sanitizer drops the whole declaration if its value has a backslash, url(, src( or
# image-set(; the server rejects @import anywhere. '<' '>' would break the markup.
BAD = re.compile(r'[<>\\]|url\(|src\(|image-set\(|@import', re.I)

# Rule that keeps one copy of the picture painted for the life of the page (zero size, invisible).
# visibility:hidden does NOT work (the clock then drifts); width/height 0 alone does not hide it.
KEEPER_CSS = 'html::after{content:var(--fm-q)/"";position:fixed;left:0;top:0;width:0;height:0;overflow:hidden}'


def slot_height(lines=LINES):
    """Picture height: LINES lines, one paragraph gap, 6px for descenders."""
    return int(lines * LH + GAP + 6)


def _factors(n):
    f, p = [], 2
    while n > 1:
        while n % p == 0:
            f.append(p); n //= p
        p += 1
    return f


_font = None
def _lines(text, width):
    """Greedy word wrap with the real Iowan Old Style metrics. Checked against Chrome on 1,980 random
    paragraphs: never fewer lines than Chrome, 18 times one more (borderline 1 vs 2 lines)."""
    global _font
    from PIL import ImageFont
    if _font is None:
        _font = ImageFont.truetype(FONT_FILE, 135)   # 10x size for sub-pixel precision
    n, line = 1, ''
    for w in text.split(' '):
        t = (line + ' ' + w) if line else w
        if _font.getlength(t) / 10 <= width or not line:
            line = t
        else:
            n, line = n + 1, w
    return n


def fit_report(quotes, width=260, lines=LINES):
    """[(index, lines p1, lines p2, ok)]; ok = p1 + p2 fit in the slot's LINES lines."""
    try:
        return [(k, _lines(a, width), _lines(b, width), _lines(a, width) + _lines(b, width) <= lines)
                for k, (a, b) in enumerate(quotes)]
    except (ImportError, OSError):     # no PIL or no font file: fall back to the measured char limit
        assert width == 260 and lines == 4, 'char fallback only measured for 260px and 4 lines'
        import warnings; warnings.warn('PIL or Iowan Old Style missing: using the 51-character fallback')
        return [(k, None, None, len(a) <= SAFE_CHARS and len(b) <= SAFE_CHARS) for k, (a, b) in enumerate(quotes)]


def _esc(p):
    return p.replace('&', '&amp;').replace('%', '%25').replace('#', '%23').replace('"', '%22')


def _fade(T, fade, dwell):
    """Shared fade centred on every change. With dwell > 0 the picture stays fully transparent for
    dwell seconds around the switch, which hides a late first switch (measured 67 ms idle, up to
    0.4 s on a loaded machine) instead of showing quote 1 for those frames."""
    if not dwell:
        return f"<animate id='z' attributeName='opacity' values='1;0;1' dur='{2*fade:g}' begin='-{fade:g};z.end+{T-2*fade:g}'/>"
    D = 2 * fade + dwell
    k = lambda x: ('%.4f' % x).rstrip('0').rstrip('.').lstrip('0') or '0'
    return (f"<animate id='z' attributeName='opacity' values='1;0;0;1' keyTimes='0;{k(fade/D)};{k((fade+dwell)/D)};1' "
            f"dur='{D:g}' begin='-{D/2:g};z.end+{T-D:g}'/>")


def _markup(texts, T, fade, width, h, dwell=0.0):
    m = len(texts)
    fs = sorted(_factors(m))
    svg = (f"<svg {NS} width='{width}' height='{h}'>"
           f"<style>div{{font:{SIZE:g}px/{LH:g}px {FAMILY};color:{COLOR};text-align:center;text-wrap:balance;columns:{width}px;gap:0}}"
           f"p{{margin:{GAP:g}px 0}}b{{display:block;break-before:column}}%3C/style>"
           + _fade(T, fade, dwell))
    if T - 2 * fade - dwell > 50:
        svg += f"<set id='h' attributeName='y' to='0' dur='{BEAT}' begin='0;h.end'/>"
    sets, unit, cols, k = [], T, 1, 0
    for r in fs:
        lvl = []
        for j in range(1, r):
            i = IDS[k]; k += 1
            lvl.append(f"<set id='{i}' attributeName='x' to='-{j*cols*width:g}' begin='{j*unit:g};{i}.end+{(r-1)*unit:g}' dur='{unit:g}'/>")
        sets.append(''.join(lvl)); unit *= r; cols *= r
    # y=-2: CSS line boxes put the first baseline 2px lower than the old <text y='13'>; this matches it.
    # Level 1 animates the foreignObject itself; every further level wraps it in a nested <svg>
    # (width 1e5 so its viewport never clips the moved strip; nested x values add up).
    body = (f"<foreignObject y='-2' width='{m*width:g}' height='{h}'>" + (sets[0] if sets else '') +
            f"<div {XNS}>" + '<b/>'.join(texts) + '</div></foreignObject>')
    for s in sets[1:]:
        body = "<svg width='1e5'>" + s + body + '</svg>'
    return svg + body + '</svg>'


def _sets_needed(m):
    return sum(p - 1 for p in _factors(m))


def quotes_svg(quotes, hold_seconds=120, fade=1.5, width=260, check_fit=True, lines=LINES, allow_repeat=False, dwell=0.8):
    """quotes: list of (paragraph1, paragraph2). Returns (css url("data:...") string, height in px)."""
    assert quotes, 'no quotes'
    assert 0 < 2 * fade + dwell < hold_seconds and dwell >= 0
    h = slot_height(lines)
    texts = []
    for p1, p2 in quotes:
        for p in (p1, p2):
            assert p and p == p.strip() and not BAD.search(p) and '\n' not in p, f'quote text not allowed: {p!r}'
        texts.append(_esc(p1) + '<p/>' + _esc(p2))
    if check_fit:
        bad = [r for r in fit_report(quotes, width, lines) if not r[3]]
        assert not bad, f'quotes too long for {lines} lines at {width}px (index, lines p1, lines p2): {bad}'
    n = len(texts)
    if allow_repeat:
        best = min((len(_markup([texts[k % n] for k in range(m)], hold_seconds, fade, width, h, dwell)), m)
                   for m in range(n, 2 * n + 1) if _sets_needed(m) <= MAX_SETS)[1]
    else:
        if _sets_needed(n) > MAX_SETS:
            ok = [m for m in range(max(1, n - 6), n + 7) if _sets_needed(m) <= MAX_SETS]
            raise ValueError(f'{n} quotes need {_sets_needed(n)} sets (limit {MAX_SETS}); use one of {ok}')
        best = n
    svg = _markup([texts[k % n] for k in range(best)], hold_seconds, fade, width, h, dwell)
    assert '"' not in svg and '\\' not in svg and 'url(' not in svg and '#' not in svg
    return 'url("data:image/svg+xml,' + svg + '")', h


def plan(quotes, hold_seconds=120):
    """How many quotes the picture really cycles through (with padding) and its full period."""
    s, _ = quotes_svg(quotes, hold_seconds, check_fit=False, allow_repeat=True)
    m = s.count('<b/>') + 1
    return {'quotes': len(quotes), 'cycled': m, 'period_s': m * hold_seconds, 'sets': s.count("<set id='") - (1 if "id='h'" in s else 0)}


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import json
    DEMO = [tuple(x) for x in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'quotes.json')))]
    s, h = quotes_svg(DEMO)
    print(len(s), h, plan(DEMO))
