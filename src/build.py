"""Builds the Focus Mat theme for T3 Code.
Usage: python3 build.py   -> writes theme.json (the installable theme file) and theme.css next to this file.
Needs Pillow. Set FM_MOCK to a folder holding index.html and empty.html test pages to also write test copies.
"""
import base64, json, os, re

OUT = os.path.dirname(os.path.abspath(__file__))
MOCK = os.environ.get('FM_MOCK', '')   # optional: test pages are only written when this folder exists

# ---------------------------------------------------------------- colour roles
COLORS = {
    "canvas": "#090909", "chrome": "#070707",
    "toolbar": "#090909", "toolbarForeground": "#d9ceb8", "toolbarBorder": "#3c3b39",
    "toolbarControl": "#ffffff0f", "toolbarControlForeground": "#d9ceb8", "toolbarControlHover": "#ffffff1f",
    "surface": "#151514", "surfaceRaised": "#201f1e", "surfaceOverlay": "#1a1a19",
    "text": "#d9ceb8", "textMuted": "#a39a89", "border": "#3c3b39", "input": "#4b4a47",
    "focus": "#d6a06c", "accent": "#d6a06c", "accentForeground": "#17100a",
    "secondary": "#272625", "secondaryForeground": "#d9ceb8",
    "muted": "#1a1a19", "mutedForeground": "#a39a89", "placeholder": "#9a9281",
    "secondaryLabel": "#b5ab98", "iconMuted": "#958c7d",
    "error": "#fb5a63", "errorForeground": "#ff8a90", "errorSurface": "#2a1016",
    "warning": "#fea31a", "warningForeground": "#ffc266", "warningSurface": "#2a1d0a",
    "update": "#d6a06c", "updateForeground": "#f0dcc0", "updateSurface": "#2a231b",
    "accentSurface": "#2a2928", "accentSurfaceForeground": "#ddd2bc",
    "messageSurface": "#0a0a0a29", "messageForeground": "#dfd4be",
    "messageAction": "#cfc4ae", "messageActionForeground": "#141413", "messageActionHover": "#e2d7c1",
    "codeBackground": "#0b0b0ad9", "codeForeground": "#d0c5b0",
    "searchMatchBackground": "#8a5a32", "searchMatchForeground": "#f6eedd",
    "searchMatchActiveBackground": "#dba565", "searchMatchActiveForeground": "#17100a",
    "sidebar": "#0a0a0a99", "sidebarForeground": "#d3c8b2", "sidebarMutedForeground": "#aaa08e",
    "sidebarControlSurface": "#ffffff0d", "sidebarRowHover": "#ffffff0d",
    "sidebarRowActive": "#ffffff17", "sidebarRowSelected": "#ffffff10", "sidebarBorder": "#3c3b39",
    "terminalBackground": "#090909", "terminalForeground": "#d0c5b0", "terminalCursor": "#e0c9a0",
    "terminalSelection": "#573c27", "terminalScrollbar": "#3c3b39", "terminalScrollbarHover": "#5d5b58",
}

# ---------------------------------------------------------------- svg helpers
def uri(svg):
    """Percent-encoded SVG data URI: double quotes outside, single quotes inside, no backslash, no url( inside."""
    s = re.sub(r'\s*\n\s*', '', svg.strip())
    assert '"' not in s and '\\' not in s and 'url(' not in s
    s = s.replace('%', '%25').replace('#', '%23').replace('<', '%3C').replace('>', '%3E')
    return 'url("data:image/svg+xml,' + s + '")'

NS = "xmlns='http://www.w3.org/2000/svg'"
def b64(svg):
    """Base64 SVG data URI, for SVG that needs an internal filter reference."""
    return 'url("data:image/svg+xml;base64,' + base64.b64encode(re.sub(r'\s*\n\s*', '', svg.strip()).encode()).decode() + '")'
GRAIN = b64(f"""<svg {NS} width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/>
<feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .55 0'/></filter><rect width='100%' height='100%' filter='url(#n)' opacity='.08'/></svg>""")
MARK_BLUR = float(os.environ.get('FM_MBLUR', '0.9'))
_BF = f"<filter id='b' x='-20%' y='-20%' width='140%' height='140%'><feGaussianBlur stdDeviation='{MARK_BLUR}'/></filter>"
PLUS = b64(f"<svg {NS} width='32' height='32' stroke='#c9c0ae'>{_BF}<g filter='url(#b)'><path d='M16 -12v56M-12 16h56' stroke-opacity='.085'/><path d='M16 12.5v7M12.5 16h7' stroke-opacity='.26'/></g></svg>")
CORNER = b64(f"""<svg {NS} width='2400' height='2400' fill='none' stroke='#c9c0ae'>{_BF}<g filter='url(#b)'>
<path d='M0 2400L2400 0' stroke-opacity='.16'/>
<path d='M0 2400L2400 1757M0 2400L2400 1014M0 2400L1386 0M0 2400L643 0' stroke-opacity='.11' stroke-dasharray='2 6'/>
<g stroke-opacity='.26' stroke-dasharray='7 6' stroke-width='1.6'>
<path d='M0 2315A85 85 0 0 1 85 2400'/><path d='M0 2048A352 352 0 0 1 352 2400'/></g></g>
</svg>""")
SCRIM = 'linear-gradient(rgba(5,5,4,.24),rgba(5,5,4,.24)),linear-gradient(90deg,rgba(5,5,4,.16),rgba(5,5,4,.56) 24%,rgba(5,5,4,.56) 76%,rgba(5,5,4,.16))'

# Frames of the wallpaper's own shader (Universe palette, wallpaper settings: frequency 1, brightness 0.95).
# Each background layer holds its frames stacked in one tall picture and jumps between them with a
# stepped transform, so the whole effect is transform and opacity only (runs on the compositor).
FIELD = os.environ.get('FM_FIELD') or os.path.join(OUT, 'frames')
FRAME_N = 17                  # z0..z16
CELL = (96, 54)
CELLS = (FRAME_N + 1) // 2    # cells per layer
STEP = 2.7                    # seconds per cross-fade (0.75x of the wallpaper's pace at its 0.55x speed setting)
HOLD = 0.1                    # seconds the top layer rests fully clear or fully solid around each jump
TICK = 0.1                    # every animation steps on this beat
NPOS = 2 * (FRAME_N - 1)      # z0..z16 and back again
LOOP = STEP * NPOS
PURPLE_KEEP = float(os.environ.get('FM_PURPLE', '0.85'))   # how much of the purple's brightness survives
WARM_GAIN = float(os.environ.get('FM_WARM', '0.81'))       # the orange band is kept, slightly lifted
PURPLE_LO = float(os.environ.get('FM_PLO', '0.55'))
PURPLE_HI = float(os.environ.get('FM_PHI', '1.0'))
WARM_SHIFT = 0.32                                           # blue taken out of the warm band so it reads orange, not pink
def _grade(im):
    """Darken by hue: purple and dim areas go most of the way to black, the warm band stays."""
    px = []
    for r, g, b in (im.get_flattened_data() if hasattr(im, 'get_flattened_data') else im.getdata()):
        w = min(1.0, max(0.0, (r - 0.72 * b) / 36.0))
        v = min(1.0, max(r, g, b) / 118.0)
        t = min(1.0, max(0.0, (v - PURPLE_LO) / (PURPLE_HI - PURPLE_LO)))
        kp = PURPLE_KEEP * t * t * (3 - 2 * t)        # purple under the threshold is black; only its peaks stay
        k = kp + (WARM_GAIN - kp) * w
        rr, gg, bb = r * k, g * k, b * k * (1 - WARM_SHIFT * w)
        ds = (1 - w) * (1 - t ** float(os.environ.get('FM_DS', '6')))                 # the dim edge of a purple blob loses its colour, so no purple cast is left on the black
        y = 0.3 * rr + 0.4 * gg + 0.3 * bb
        rr, gg, bb = rr + (y - rr) * ds, gg + (y - gg) * ds, bb + (y - bb) * ds
        px.append((min(255, int(rr + .5)), min(255, int(gg + .5)), min(255, int(bb + .5))))
    out = im.copy(); out.putdata(px); return out
def _sprite(ids):
    import io
    from PIL import Image
    sp = Image.new('RGB', (CELL[0], CELL[1] * CELLS))
    for k, i in enumerate(ids):
        sp.paste(_grade(Image.open(os.path.join(FIELD, f'z{i}.png')).convert('RGB').resize(CELL, Image.LANCZOS)), (0, CELL[1] * k))
    b = io.BytesIO(); sp.save(b, 'WEBP', quality=60, method=6)
    return 'url("data:image/webp;base64,' + base64.b64encode(b.getvalue()).decode() + '")'
_even = list(range(0, FRAME_N, 2)); _odd = list(range(1, FRAME_N, 2))
SB = _sprite(_even); SA = _sprite(_odd + [_odd[-1]] * (CELLS - len(_odd)))
def _pct(x):
    return ('%.3f' % x).rstrip('0').rstrip('.') + '%'
def _jump(name, items):
    """items: (position index where the layer jumps, frame index). Groups equal frames into one keyframe selector."""
    by = {}
    for pos, fr in items:
        by.setdefault(fr // 2, []).append(pos)
    return '@keyframes ' + name + '{' + ''.join(
        ','.join(_pct(100.0 * x / NPOS) for x in sorted(set(ps))) + '{transform:translateY(' + ('0' if cell == 0 else '-' + _pct(100.0 * cell / CELLS)) + ')}'
        for cell, ps in sorted(by.items())) + '}'
_seq = lambda k: k if k < FRAME_N else NPOS - k
# base layer shows the even positions, top layer the odd ones; each jumps to its next frame while the other covers it
KF_B = _jump('fm-jb', [(0, 0), (NPOS, 0), (NPOS - 1, 0)] + [(k - 1, _seq(k)) for k in range(2, NPOS, 2)])
KF_A = _jump('fm-ja', [(NPOS, 1)] + [(k - 1, _seq(k)) for k in range(1, NPOS, 2)])
FADE_STEPS = round((STEP - 2 * HOLD) / TICK)
DRIFT = 120
ORIGIN = _pct(50.0 / CELLS)
def _y(v):   # a vertical shift given in percent of one cell
    return _pct(v / CELLS)

QUOTES = [
    (['Let the noise settle like silt', 'in still water.'], ['What remains is the one thing in front', 'of you, and it was always enough.']),
    (['The river does not hurry,', 'and still it arrives.'], ['Let the work move', 'at the speed of attention.']),
    (['Nothing is lost by going slowly.'], ['What you tend with a quiet mind', 'takes its own shape.']),
]
Q_HOLD = int(os.environ.get('FM_QHOLD', '240'))   # seconds each one stays
Q_FADE = 1.5
Q_W, Q_LH, Q_GAP = 260, 20.5, 9.0
Q_H = int(max(len(a) + len(b) for a, b in QUOTES) * Q_LH + Q_GAP + 6)
def _quotes_svg():
    ids = 'abcdef'
    out = [f"<svg {NS} width='{Q_W}' height='{Q_H}' font-family='Iowan Old Style,Palatino Linotype,Palatino,Georgia,serif' font-size='13.5' fill='#d9ceb8'>"]
    for k, (p1, p2) in enumerate(QUOTES):
        i_in, i_out = ids[2 * k], ids[2 * k + 1]
        prev_out = ids[(2 * k - 1) % 6]
        begin_in = ('0s;' if k == 0 else '') + prev_out + '.end'
        out.append("<g opacity='0'>")
        out.append(f"<animate id='{i_in}' attributeName='opacity' from='0' to='1' dur='{Q_FADE}s' begin='{begin_in}' fill='freeze'/>")
        out.append(f"<animate id='{i_out}' attributeName='opacity' from='1' to='0' dur='{Q_FADE}s' begin='{i_in}.end+{Q_HOLD}s' fill='freeze'/>")
        y = 13.0
        out.append('<text>')
        for ln in p1:
            out.append(f"<tspan x='0' y='{y:g}'>{ln}</tspan>"); y += Q_LH
        y += Q_GAP
        for ln in p2:
            out.append(f"<tspan x='0' y='{y:g}'>{ln}</tspan>"); y += Q_LH
        out.append('</text></g>')
    out.append('</svg>')
    return uri(''.join(out))
QSVG = _quotes_svg()

CSS = f"""
html[data-theme-id]{{--fm-serif:Constantia,"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;--fm-line:rgba(255,255,255,.1);--fm-q:{QSVG};--fm-g:{GRAIN}}}
html.dark[data-theme-id]{{--guest:#b5895f;--guest-foreground:#dcb98f;--color-violet-300:#e2d7c1;--color-indigo-300:#d6a06c;--color-fuchsia-300:#e2d7c1;--app-scrollbar-thumb:#ffffff24;--app-scrollbar-thumb-hover:#ffffff47}}
diffs-container{{--code-background:#090908}}
::selection{{background:rgba(196,132,78,.42)}}
:is(.chat-markdown,.composer-tiptap) ::selection{{color:#f6eedd}}
::highlight(t3-assistant-citation-comment){{background-color:rgba(196,132,78,.42)}}
::highlight(t3-assistant-citation){{background-color:color-mix(in oklab,#c4844e calc(var(--assistant-citation-highlight-opacity)*100%),transparent)}}

[data-slot="sidebar-wrapper"]{{position:relative;isolation:isolate;overflow:clip}}
[data-slot="sidebar-wrapper"]::before,[data-slot="sidebar-wrapper"]::after{{content:"";position:absolute;left:-9%;right:-9%;top:-9%;height:{_pct(118*CELLS)};z-index:-1;pointer-events:none;background:0 0/100% 100% no-repeat;transform-origin:50% {ORIGIN};scale:1.14}}
[data-slot="sidebar-wrapper"]::before{{background-image:{SB}}}
[data-slot="sidebar-wrapper"]::after{{background-image:{SA};opacity:0}}
@media (prefers-reduced-motion:no-preference){{
[data-slot="sidebar-wrapper"]::before{{animation:fm-drift {DRIFT}s steps({round(DRIFT/TICK)}) infinite alternate,fm-jb {LOOP:g}s step-end infinite}}
[data-slot="sidebar-wrapper"]::after{{animation:fm-drift {DRIFT}s steps({round(DRIFT/TICK)}) infinite alternate,fm-ja {LOOP:g}s step-end infinite,fm-fade {STEP:g}s steps({FADE_STEPS}) infinite alternate}}
[data-base-ui-inert] [data-slot="sidebar-wrapper"]::before,[data-base-ui-inert] [data-slot="sidebar-wrapper"]::after{{animation-play-state:paused}}
}}
@keyframes fm-drift{{from{{translate:-2.5% -{_y(1.5)};rotate:-2deg;scale:1.1}}to{{translate:2.5% {_y(2)};rotate:3deg;scale:1.24}}}}
@keyframes fm-fade{{0%,{_pct(100*HOLD/STEP)}{{opacity:0}}{_pct(100-100*HOLD/STEP)},100%{{opacity:1}}}}
{KF_B}
{KF_A}

[data-slot="sidebar-inset"]:has([data-chat-column]),[data-slot="sidebar-inset"]>div,html[data-theme-id] [data-chat-header],[data-workspace-area="conversation"],[data-timeline-loading]{{background-color:transparent}}
[data-chat-column]{{background:var(--fm-g),linear-gradient(180deg,rgba(255,255,255,.03),transparent 140px),{SCRIM},{CORNER} left bottom no-repeat,{PLUS} 0 0;box-shadow:inset 0 1px 0 rgba(255,255,255,.1)}}
html[data-theme-id] [data-chat-header]{{background-image:linear-gradient(90deg,transparent,rgba(255,255,255,.16) 30%,rgba(240,150,110,.4) 70%,transparent),linear-gradient(rgba(5,5,4,.42),rgba(5,5,4,.42));background-size:100% 1px,100% 100%;background-position:0 100%,0 0;background-repeat:no-repeat}}
[data-slot="sidebar-inner"]{{background-image:var(--fm-g),linear-gradient(180deg,rgba(255,255,255,.035),transparent 40%);background-size:auto;box-shadow:inset -1px 0 0 rgba(255,255,255,.07),inset 0 1px 0 rgba(255,255,255,.1)}}
[data-app-sidebar] [data-slot="sidebar-footer"]::before{{content:"Stay Focused.";font:400 14px/1 var(--fm-serif);color:#d9ceb8;opacity:.62;padding:6px 8px 2px}}

[data-chat-composer-stack] h1{{font-family:var(--fm-serif);letter-spacing:0}}
[data-timeline-row-kind] div.chat-markdown{{font-size:.90625rem;line-height:1.7}}
.chat-markdown :is(h1,h2,h3,h4,h5,h6){{font-weight:650;letter-spacing:-.01em}}
html[data-theme-id] :is(.chat-markdown,.composer-tiptap) .chat-markdown-codeblock{{border-color:var(--fm-line)}}
span[class*="app-theme-message-action,var(--color-emerald"]{{color:#fda4af}}
[data-user-message-bubble].bg-message{{border:1px solid rgba(238,232,220,.1);background:var(--fm-g),linear-gradient(180deg,rgba(255,255,255,.035),transparent 40%),rgba(7,7,6,.16);backdrop-filter:blur(16px) saturate(1.08)}}
[data-thread-details-card]{{background:var(--fm-g),linear-gradient(180deg,rgba(255,255,255,.035),transparent 40%),rgba(7,7,6,.36)}}
[data-slot="composer-shell"]{{anchor-scope:--fm-m;--glass-opacity:20%;--chat-composer-glass-surface:#070706;--chat-composer-highlight:transparent;--chat-composer-outline:rgba(238,232,220,.1)}}
[data-slot="composer-shell"]::before{{background-image:var(--fm-g),linear-gradient(180deg,rgba(255,255,255,.035),transparent 40%)}}
[data-chat-composer-main-surface]{{anchor-name:--fm-m}}
[data-slot="composer-shell"]:is([data-context-row],:has([data-composer-banner-surface=attached]))::before{{display:block;top:anchor(--fm-m top,0px)}}
[data-slot="composer-shell"][data-context-row]::before{{bottom:-12px}}
[data-slot="composer-shell"]:has([data-composer-banner-surface=attached]) [data-chat-composer-main-surface],[data-context-row] [data-slot="composer-context-strip"]{{background:none;backdrop-filter:none}}
[data-context-row] [data-slot="composer-host"]::before{{display:none}}

[data-slot=project-monogram]{{--color-indigo-400:#b8a88c;--color-violet-400:#c9b48f;--color-purple-400:#c49a7a;--color-fuchsia-400:#d08a7a}}
[data-markdown-alert=important]{{border-color:rgba(207,196,174,.7)}}
[data-markdown-alert=important]>[data-markdown-alert-label]{{color:#e2d7c1}}
pre :is([style*="9d6afb" i],[style*="157, 106, 251"]){{color:#e6d5b0!important}}
pre :is([style*="d568ea" i],[style*="213, 104, 234"]){{color:#d6a06c!important}}
[data-pierre-icon]:is([data-icon-token=css],[data-icon-token=bootstrap],[data-icon-token=eslint],[data-icon-token=terraform],[data-icon-token=wasm]){{color:#c9b48f!important}}
[data-pierre-icon]:is([data-icon-token=astro],[data-icon-token=database],[data-icon-token=vite]){{color:#d6a06c!important}}
file-tree-container{{--trees-icon-indigo:#c9b48f;--trees-icon-purple:#d6a06c}}
[role=switch][data-unchecked]{{background-color:#6a6864}}
[data-chat-canvas]:has(>[data-chat-composer-overlay].inset-0) [data-timeline-loading]::after{{content:"Stay Focused.";position:absolute;right:7%;bottom:calc(11% + {Q_H + 14}px);font:400 clamp(28px,3.4vw,46px)/1 var(--fm-serif);color:#e2d7c1;opacity:.9;pointer-events:none}}
[data-chat-canvas]:has(>[data-chat-composer-overlay].inset-0) [data-timeline-loading]::before{{content:var(--fm-q);position:absolute;right:7%;bottom:11%;width:{Q_W}px;height:{Q_H}px;opacity:.7;pointer-events:none}}
[data-slot="sidebar"][data-mobile="true"]{{background-color:#070706}}
[data-chat-canvas]{{anchor-scope:--fm-c,--fm-d;anchor-name:--fm-c}}
[data-thread-details-panel="inline"][data-density="full"]{{anchor-name:--fm-d}}
[data-thread-details-panel="inline"][data-density="full"]::before{{content:"Stay Focused.";position:fixed;position-anchor:--fm-d;position-visibility:no-overflow;align-self:end;left:calc(anchor(left) + 14px);top:calc(anchor(bottom) + 28px);bottom:calc(anchor(--fm-c bottom) + {Q_H + 38}px);height:24px;font:400 24px/1 var(--fm-serif);color:#e2d7c1;opacity:.85;white-space:nowrap;pointer-events:none}}
[data-thread-details-panel="inline"][data-density="full"]::after{{content:var(--fm-q);position:fixed;position-anchor:--fm-d;position-visibility:no-overflow;align-self:end;left:calc(anchor(left) + 14px);top:calc(anchor(bottom) + 66px);bottom:calc(anchor(--fm-c bottom) + 24px);width:{Q_W}px;height:{Q_H}px;opacity:.68;pointer-events:none}}
@media (max-height:47rem){{[data-timeline-loading]::before,[data-timeline-loading]::after{{display:none}}}}
[data-chat-column]:has([data-thread-details-panel="inline"]) [data-timeline-loading]::before,[data-chat-column]:has([data-thread-details-panel="inline"]) [data-timeline-loading]::after{{display:none}}
[data-chat-canvas]>div.inset-0:not([data-workspace-area])~[data-thread-details-panel]::before,[data-chat-canvas]>div.inset-0:not([data-workspace-area])~[data-thread-details-panel]::after{{display:none}}
@media (max-height:46rem){{[data-thread-details-panel]::before,[data-thread-details-panel]::after{{display:none}}}}
"""

MOCKFIX = '[data-slot="scroll-area"]{max-height:calc(100vh - 134px - 84px - 30px)!important;overflow:hidden!important}'

def minify(css):
    css = re.sub(r'\n+', '\n', css.strip())
    return css

def var_name(role):
    if role == 'terminalSelection':
        return '--app-theme-terminal-selection-background'
    return '--app-theme-' + re.sub(r'([A-Z])', lambda m: '-' + m.group(1).lower(), role)

def main():
    css = minify(CSS) + open(os.path.join(OUT, 'clawd-set.css')).read()   # layout rules adapted from T3 Clawd (MIT), see CREDITS.md
    theme = {"version": 1, "name": "Focus Mat", "appearance": "dark", "canvas": COLORS['canvas'],
             "accent": COLORS['accent'], "colors": COLORS, "css": css}
    open(os.path.join(OUT, 'theme.css'), 'w').write(css)
    js = json.dumps(theme, indent=2)
    open(os.path.join(OUT, 'theme.json'), 'w').write(js)
    print('css chars', len(css), '/ 20000 | json bytes', len(js.encode()), '/ 32768')
    assert len(css) <= 20000 and len(js.encode()) <= 32768
    if not os.path.isdir(MOCK):
        return
    vars_css = 'html[data-theme-id]{' + ';'.join(f'{var_name(k)}:{v}' for k, v in COLORS.items()) + '}'
    for page in ('index', 'empty'):
        src = open(os.path.join(MOCK, page + '.html')).read()
        a = src.index('<style id="theme-vars">'); b = src.index('</style>', a)
        src = src[:a] + '<style id="theme-vars">\n' + vars_css + '\n' + src[b:]
        a = src.index('<style id="theme-css">'); b = src.index('</style>', a)
        for tag, extra in [('', '')] + [(f'-t{i}', f'[data-slot="sidebar-wrapper"]::before,[data-slot="sidebar-wrapper"]::after{{animation-delay:-{d}s!important;animation-play-state:paused!important}}') for i, d in enumerate([0, 1, 2, 11, 31])]:
            out = src[:a] + '<style id="theme-css">\n' + css + '\n' + extra + '\n' + MOCKFIX + '\n' + src[b:]
            open(os.path.join(OUT, f'test-{page}{tag}.html'), 'w').write(out)

if __name__ == '__main__':
    main()
