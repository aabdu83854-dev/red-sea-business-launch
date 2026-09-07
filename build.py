#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the deployable site from the single editable source, src/site.html.

Why this exists
---------------
The site used to be one self-contained index.html served under a hash router
(/#/yemen). Google discards everything after "#", so every route collapsed to
the site root and none of the content pages could rank. This build emits one
real HTML file per route, so /yemen is a genuine URL with its own <title>,
description, canonical and Open Graph tags.

The host (Cloudflare Workers static assets) drops the .html extension, so
"yemen.html" is served at "/yemen" — verified: /index returns 307 to /.

CSS and JS are lifted into app.css and app.js so the 21 route files stay small
and the browser caches the heavy part once instead of per route.

Edit src/site.html. Never edit the generated files.
"""
import json, os, re, shutil, sys, hashlib

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(ROOT, "src", "site.html")
DOMAIN = "red-sea-business-launch.a-abdu83854.workers.dev"

# Routes that get a crawlable file. "/passport" is an internal tool: it needs to
# work, but it must not be indexed.
INDEXABLE = ["/", "/deals", "/packages", "/calculator", "/compliance",
             "/supplier-check", "/yemen", "/qualify", "/consultation",
             "/about", "/faq", "/contact", "/legal"]
NOINDEX   = ["/thanks", "/passport"]

# Files the build owns. Removed before each run so a renamed route cannot leave
# a stale page live.
GENERATED = ["index.html", "app.css", "app.js"]

# filled in by main(): "/assets/x.png" -> content hash, used by version_assets
ASSET_VERS = {}
EXPECTED_ASSETS = ["logo.png", "favicon.png", "og.png"]


def die(msg):
    sys.exit("build.py: " + msg)


def read_source():
    if not os.path.exists(SRC):
        die("missing source: src/site.html")
    return open(SRC, encoding="utf-8").read()


def extract(s, open_tag, close_tag, start=0):
    i = s.find(open_tag, start)
    if i < 0:
        die("could not find " + open_tag)
    j = s.find(close_tag, i)
    if j < 0:
        die("unterminated " + open_tag)
    return i, j + len(close_tag), s[i + len(open_tag): j]


def parse_meta(js):
    """META in the source is a JSON-compatible object literal: quoted keys,
    values are [[ar,en],[ar,en]]. Parsed rather than duplicated here so the
    titles can never drift out of sync with the app."""
    i = js.find("const META = {")
    if i < 0:
        die("could not find META in the source")
    i = js.find("{", i)
    depth, j = 0, i
    while j < len(js):
        if js[j] == "{":
            depth += 1
        elif js[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    raw = js[i:j + 1]
    # strip // comments and trailing commas, both legal in JS and not in JSON
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
    try:
        return json.loads(raw)
    except Exception as e:
        die("META is not JSON-parseable (%s). Keep it to quoted keys and "
            "plain string arrays." % e)


def asset_versions():
    """/assets/* is cached for 30 days but the filenames are not content-hashed,
    so every reference carries ?v=<hash>. Without this, replacing a logo would
    leave the old one in visitors' browsers for a month."""
    out = {}
    d = os.path.join(ROOT, "assets")
    for f in sorted(os.listdir(d)):
        full = os.path.join(d, f)
        if os.path.isfile(full):
            out["/assets/" + f] = hashlib.sha256(
                open(full, "rb").read()).hexdigest()[:8]
    # A missing asset would silently produce a page whose reference carries no
    # ?v= stamp — the build would still "succeed" but differ from every other
    # machine's output. That already happened once; name them explicitly.
    missing = [f for f in EXPECTED_ASSETS if "/assets/" + f not in out]
    if missing:
        die("assets/ is missing %s — the working copy is incomplete, so this "
            "build would not match the committed one" % ", ".join(missing))
    return out


def version_assets(text, vers):
    for path, h in vers.items():
        text = text.replace(path + "?v=", path + "\0")      # never double-stamp
        text = text.replace(path, path + "?v=" + h)
        text = text.replace(path + "\0", path + "?v=")
    return text


def deals_from(js):
    """Parse window.DEALS so each deal page can carry its own title and
    description. Returns [(id, ar_title, ar_desc), ...]."""
    key = "window.DEALS="
    i = js.find(key)
    if i < 0:
        die("found no window.DEALS in the source")
    try:
        data, _ = json.JSONDecoder().raw_decode(js, i + len(key))
    except Exception as e:
        die("window.DEALS is not JSON-parseable (%s)" % e)
    if not data:
        die("window.DEALS is empty")
    out = []
    for d in data:
        for k in ("id", "name", "short"):
            if k not in d:
                die("a deal is missing %r" % k)
        out.append((d["id"], d["name"][0], d["short"][0]))
    return out


def esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def shell(route, title, desc, body, css_href, js_href, index_ok=True, crumbs=None):
    """The <body> markup is lifted verbatim from the source rather than
    re-written here: a hand-kept copy silently drifts (it already cost us the
    scroll-progress bar's id once)."""
    url = "https://%s%s" % (DOMAIN, "/" if route == "/" else route)
    robots = ("index,follow,max-image-preview:large,max-snippet:-1"
              if index_ok else "noindex,follow")
    # Real text for a crawler and for anyone with JS off.
    ns = ('<noscript><div style="max-width:640px;margin:0 auto;padding:48px 24px;'
          'font-family:system-ui,sans-serif;line-height:1.8;text-align:right;direction:rtl">'
          '<h1 style="font-size:1.5rem;margin:0 0 12px">%s</h1><p style="margin:0 0 10px">%s</p>'
          '<p style="margin:18px 0 0">يحتاج هذا الموقع تفعيل JavaScript لعرض محتواه بالكامل. '
          'This site needs JavaScript enabled.</p>'
          '<p style="margin:8px 0 0">للتواصل: <a href="mailto:deals@redseaglobal.com">'
          'deals@redseaglobal.com</a></p></div></noscript>') % (esc(title), esc(desc))
    # A branded placeholder inside #app, so a slow connection sees the site
    # loading instead of a blank white page. render() overwrites #app wholesale.
    boot = ('<div id="boot">'
            '<div class="bh" aria-hidden="true"><span class="bm"></span>'
            '<span class="bt">\u0631\u064a\u062f \u0633\u064a \u0628\u0632\u0646\u0633 \u0644\u0627\u0646\u0634'
            '<small>Red Sea Business Launch</small></span></div>'
            '<div class="bw"><div aria-hidden="true">'
            '<div class="sk s1"></div><div class="sk s2"></div>'
            '<div class="sk s3"></div><div class="sk s4"></div>'
            '<div class="sg"><div class="sk sc"></div><div class="sk sc"></div>'
            '<div class="sk sc"></div></div></div>'
            '<p class="bfall">\u0625\u0646 \u0644\u0645 \u062a\u0638\u0647\u0631 \u0627\u0644\u0635\u0641\u062d\u0629\u060c '
            '\u0641\u0642\u062f \u064a\u0643\u0648\u0646 \u0627\u062a\u0635\u0627\u0644\u0643 \u0628\u0637\u064a\u0626\u064b\u0627 '
            '\u0623\u0648 \u0627\u0644\u0645\u062a\u0635\u0641\u0651\u062d \u064a\u062d\u062c\u0628 '
            '\u0627\u0644\u0633\u0643\u0631\u0628\u062a\u0627\u062a. '
            '\u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0646\u0627 \u0645\u0628\u0627\u0634\u0631\u0629: '
            '<a href="mailto:deals@redseaglobal.com">deals@redseaglobal.com</a><br>'
            '<span dir="ltr">If this page does not appear, your connection may be slow or '
            'scripts are blocked. Reach us at '
            '<a href="mailto:deals@redseaglobal.com">deals@redseaglobal.com</a></span></p>'
            '</div></div>')
    assert '<div id="app"></div>' in body, "the #app container moved"
    body = body.replace('<div id="app"></div>',
                        '<div id="app">' + boot + '</div>\n' + ns, 1)
    # Breadcrumbs are one of the structured-data types Google still renders in
    # results, so a deal page shows a readable trail instead of a bare URL.
    crumb_ld = ""
    if crumbs:
        items = ",".join(
            '{"@type":"ListItem","position":%d,"name":%s,"item":"https://%s%s"}'
            % (i + 1, json.dumps(n, ensure_ascii=False), DOMAIN, u)
            for i, (n, u) in enumerate(crumbs))
        crumb_ld = ('\n<script type="application/ld+json">'
                    '{"@context":"https://schema.org","@type":"BreadcrumbList",'
                    '"itemListElement":[%s]}</script>' % items)
    return version_assets(f"""<!doctype html><html lang="ar" dir="rtl"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta name="theme-color" content="#062B57">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{url}">
<link rel="icon" href="/assets/favicon.png">
<link rel="apple-touch-icon" href="/assets/favicon.png">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Red Sea Business Launch">
<meta property="og:locale" content="ar_SA">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="https://{DOMAIN}/assets/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap" media="print" onload="this.media='all'">
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap"></noscript>
<link rel="stylesheet" href="{css_href}">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Organization","name":"Red Sea Business Launch","url":"https://{DOMAIN}","logo":"https://{DOMAIN}/assets/logo.png","parentOrganization":{{"@type":"Organization","name":"Red Sea Global Trading Co. Ltd."}},"areaServed":["SA","AE","OM","QA","KW","BH","YE"],"address":{{"@type":"PostalAddress","addressCountry":"CN"}}}}</script>{crumb_ld}
</head><body>
{body}
<script src="{js_href}" defer></script>
</body></html>
""", ASSET_VERS)


def main():
    s = read_source()

    # --- lift the stylesheet -------------------------------------------------
    c0, c1, css = extract(s, "<style>", "</style>")

    # --- lift the application script -----------------------------------------
    # There are two plain <script> blocks. The first carries the inlined logo
    # data URI followed by the DEALS dataset; the second is the application.
    # The logo is dropped (it is served from /assets/logo.png instead, saving
    # 111 KB on every page) and the dataset is kept.
    d0 = s.find("<script>", c1)
    if d0 < 0:
        die("could not locate the data script")
    _, _, data = extract(s, "<script>", "</script>", d0)
    cut = data.find('";window.DEALS=')
    if cut < 0:
        die("could not separate the logo from the DEALS dataset")
    data = data[cut + 2:]
    if "window.DEALS" not in data:
        die("DEALS dataset went missing while stripping the logo")

    k = s.rfind("<script>")
    if k <= d0:
        die("could not locate the application script")
    j0, j1, app = extract(s, "<script>", "</script>", k)
    js = data + "\n" + app

    # --- lift the body markup ------------------------------------------------
    bs = s.find(">", s.find("<body")) + 1
    if bs <= 0 or bs > d0:
        die("could not locate <body>")
    body = s[bs:d0]
    # The stylesheet lives inside <body> in the source; it has already been
    # lifted to app.css, so it must not be copied into all 21 route files.
    body = body.replace(s[c0:c1], "")
    body = re.sub(r"<noscript>.*?</noscript>", "", body, flags=re.S).strip()
    if "<style>" in body:
        die("a <style> block survived into the body markup")
    for need in ('id="app"', 'id="prog"', 'class="skip"'):
        if need not in body:
            die("body markup is missing " + need)

    meta = parse_meta(js)
    deals = deals_from(js)

    for r in INDEXABLE:
        if r not in meta:
            die("route %s has no META entry in the source" % r)

    # --- fingerprint so a deploy can never serve a stale cached bundle --------
    global ASSET_VERS
    ASSET_VERS = asset_versions()
    css = version_assets(css, ASSET_VERS)
    js = version_assets(js, ASSET_VERS)

    css_h = hashlib.sha256(css.encode()).hexdigest()[:8]
    js_h  = hashlib.sha256(js.encode()).hexdigest()[:8]
    css_name, js_name = "app.%s.css" % css_h, "app.%s.js" % js_h

    # --- clear previously generated files ------------------------------------
    for f in os.listdir(ROOT):
        if re.fullmatch(r"app\.[0-9a-f]{8}\.(css|js)", f):
            os.remove(os.path.join(ROOT, f))
    for r in INDEXABLE + NOINDEX:
        p = os.path.join(ROOT, ("index" if r == "/" else r.lstrip("/")) + ".html")
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(os.path.join(ROOT, "deal"), ignore_errors=True)

    open(os.path.join(ROOT, css_name), "w", encoding="utf-8").write(css)
    open(os.path.join(ROOT, js_name), "w", encoding="utf-8").write(js)

    css_href, js_href = "/" + css_name, "/" + js_name
    written = [css_name, js_name]

    # --- one real page per route ---------------------------------------------
    def emit(path_on_disk, route, title, desc, index_ok=True, crumbs=None):
        full = os.path.join(ROOT, path_on_disk)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w", encoding="utf-8").write(
            shell(route, title, desc, body, css_href, js_href, index_ok, crumbs))
        written.append(path_on_disk)

    for r in INDEXABLE:
        m = meta[r]
        emit(("index" if r == "/" else r.lstrip("/")) + ".html", r, m[0][0], m[1][0])

    for r in NOINDEX:
        m = meta.get(r) or [["Red Sea Business Launch"], [""]]
        emit(r.lstrip("/") + ".html", r, m[0][0], m[1][0], index_ok=False)

    # A styled 404 for hosts configured to serve one. Harmless where the host
    # returns a bare 404 instead.
    emit("404.html", "/404",
         "الصفحة غير موجودة — Red Sea Business Launch",
         "الرابط الذي فتحته غير موجود. تصفّح الصفقات الجاهزة أو حاسبة التكلفة أو تواصل معنا.",
         index_ok=False)

    deals_name = meta.get("/deals", [["الصفقات الجاهزة"]])[0][0].split(" — ")[0]
    for did, dtitle, ddesc in deals:
        emit("deal/%s.html" % did, "/deal/" + did,
             dtitle + " — Red Sea Business Launch", ddesc,
             crumbs=[("Red Sea Business Launch", "/"),
                     (deals_name, "/deals"),
                     (dtitle, "/deal/" + did)])

    # --- sitemap -------------------------------------------------------------
    urls = "\n".join(
        '  <url><loc>https://%s%s</loc><changefreq>%s</changefreq><priority>%s</priority></url>'
        % (DOMAIN, "/" if r == "/" else r,
           "weekly" if r in ("/", "/deals", "/compliance", "/yemen") else "monthly",
           "1.0" if r == "/" else "0.9" if r in ("/deals", "/compliance", "/yemen", "/supplier-check") else "0.7")
        for r in INDEXABLE)
    urls += "\n" + "\n".join(
        '  <url><loc>https://%s/deal/%s</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>'
        % (DOMAIN, d[0]) for d in deals)
    open(os.path.join(ROOT, "sitemap.xml"), "w", encoding="utf-8").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>\n' % urls)
    written.append("sitemap.xml")

    open(os.path.join(ROOT, "robots.txt"), "w", encoding="utf-8").write(
        "User-agent: *\nAllow: /\nDisallow: /p/\nDisallow: /passport\n\n"
        "Sitemap: https://%s/sitemap.xml\n" % DOMAIN)
    written.append("robots.txt")

    # --- _headers ------------------------------------------------------------
    # Cloudflare parses this as configuration; it is never served to visitors.
    # The app bundle is content-hashed, so it can be cached permanently: a new
    # build produces a new filename. Without this it is revalidated on every
    # single page view.
    open(os.path.join(ROOT, "_headers"), "w", encoding="utf-8").write(
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  X-Frame-Options: SAMEORIGIN\n"
        "  Content-Security-Policy: frame-ancestors 'self'\n"
        "  Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=()\n"
        "  Cross-Origin-Opener-Policy: same-origin\n"
        "\n"
        "/app.*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
        "\n"
        "# every reference to these carries ?v=<content hash>, so a changed file\n"
        "# is a changed URL and this can be cached permanently\n"
        "/assets/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
        "\n"
        "# exported shipment passports are per-customer documents\n"
        "/p/*\n"
        "  X-Robots-Tag: noindex, nofollow\n"
        "  Referrer-Policy: no-referrer\n")
    written.append("_headers")

    total = sum(os.path.getsize(os.path.join(ROOT, f)) for f in written)
    print("built %d files, %.0f KB total" % (len(written), total / 1024))
    print("  %s  %.0f KB" % (css_name, os.path.getsize(os.path.join(ROOT, css_name)) / 1024))
    print("  %s   %.0f KB" % (js_name, os.path.getsize(os.path.join(ROOT, js_name)) / 1024))
    print("  %d route pages, %d deal pages" % (len(INDEXABLE) + len(NOINDEX), len(deals)))


if __name__ == "__main__":
    main()
