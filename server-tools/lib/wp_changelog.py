"""Bounded, print-only WordPress.org plugin release notes. Never execute HTML."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request

MAX_BYTES = 1024 * 1024
TIMEOUT = 8
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]*\Z')
VERSION = re.compile(r'\d+(?:\.\d+)*\Z')
HEADING = re.compile(r'^(?:version\s+|v)?(\d+(?:\.\d+)*)(?=$|\s|[(:\u2013\u2014-])', re.I)


def clean(text):
    """Remove terminal controls, including escape and bidirectional controls."""
    return ''.join(c for c in text if c in '\n\t' or not unicodedata.category(c).startswith('C'))


def version(text):
    if not VERSION.fullmatch(text) or len(text) > 80:
        raise ValueError('Non-numeric release version')
    parts = tuple(int(x) for x in text.split('.'))
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


def compare(left, right):
    width = max(len(left), len(right))
    left, right = left + (0,) * (width - len(left)), right + (0,) * (width - len(right))
    return (left > right) - (left < right)


class NotesParser(HTMLParser):
    """Recognise version headings; exclude unversioned sections and scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.releases = []
        self.current = None
        self.heading = None
        self.heading_text = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.heading = tag
            self.heading_text = []
            self.current = None
        elif self.heading is None and self.current is not None:
            if tag in ('p', 'li', 'br', 'div', 'ul', 'ol', 'pre'):
                self.current[1].append('\n' + ('- ' if tag == 'li' else ''))
            if tag == 'a':
                target = dict(attrs).get('href', '')
                if target.startswith(('https://', 'http://')):
                    self.current[1].append(' [' + clean(target) + '] ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag == self.heading:
            title = ' '.join(''.join(self.heading_text).split())
            match = HEADING.match(title)
            if match:
                self.current = [match.group(1), []]
                self.releases.append(self.current)
            self.heading = None
        elif tag in ('p', 'li', 'div', 'pre') and self.current is not None:
            self.current[1].append('\n')

    def handle_data(self, data):
        if self.hidden:
            return
        if self.heading is not None:
            self.heading_text.append(data)
        elif self.current is not None:
            self.current[1].append(clean(data))


def parse_notes(html, old, new):
    low, high = version(old), version(new)
    if compare(low, high) >= 0:
        raise ValueError('No increasing numeric version range')
    parser = NotesParser()
    parser.feed(html)
    selected = []
    boundaries = set()
    seen = set()
    for number, fragments in parser.releases:
        key = version(number)
        if compare(key, low) == 0:
            boundaries.add('old')
        if compare(key, high) == 0:
            boundaries.add('new')
        if compare(key, low) > 0 and compare(key, high) <= 0 and key not in seen:
            lines = [' '.join(line.split()) for line in ''.join(fragments).splitlines() if line.strip()]
            selected.append((number, lines))
            seen.add(key)
    # Numeric tuple order is correct here after padding to a common width.
    width = max((len(version(number)) for number, _ in selected), default=1)
    selected.sort(key=lambda item: version(item[0]) + (0,) * (width - len(version(item[0]))), reverse=True)
    return selected, boundaries


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A plugin API response must not cause requests to a third-party host.
        return None


def fetch_plugin(slug):
    if not SLUG.fullmatch(slug):
        raise ValueError('Invalid plugin slug')
    query = urllib.parse.urlencode({
        'action': 'plugin_information', 'request[slug]': slug,
        'request[fields][sections]': 'true', 'request[fields][versions]': 'false',
    })
    request = urllib.request.Request('https://api.wordpress.org/plugins/info/1.2/?' + query,
                                     headers={'User-Agent': 'wp-hosting-changelog/1', 'Accept': 'application/json'})
    started = time.monotonic()
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=TIMEOUT) as response:
        chunks = []
        size = 0
        while True:
            if time.monotonic() - started > TIMEOUT:
                raise TimeoutError('Changelog request deadline')
            chunk = response.read(min(16384, MAX_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('Changelog response too large')
    data = json.loads(b''.join(chunks))
    if not isinstance(data, dict) or data.get('slug') != slug:
        raise ValueError('Plugin not found in WordPress.org')
    html = data.get('sections', {}).get('changelog')
    if not isinstance(html, str) or not html.strip():
        raise ValueError('No published changelog')
    return html


def component_notes(kind, name, old, new):
    title = clean(f'CHANGELOG {kind}:{name} {old} -> {new}')
    if kind == 'core':
        return title + '\nChangelog unavailable: core release-note aggregation is not supported.\nSource: https://wordpress.org/news/category/releases/\n', None
    if kind != 'plugin':
        return title + '\nChangelog unavailable: no standard theme release-note source. Review the author documentation.\n', None
    if not SLUG.fullmatch(name):
        return title + '\nChangelog unavailable: invalid plugin slug.\n', None
    source = f'https://wordpress.org/plugins/{name}/#developers'
    try:
        selected, boundaries = parse_notes(fetch_plugin(name), old, new)
    except Exception:
        # Remote exceptions can contain URLs or arbitrary response content. Never echo them.
        return title + '\nChangelog unavailable: source unavailable, unsupported format, or non-numeric versions.\nSource: ' + source + '\n', None
    if not selected:
        return title + '\nChangelog unavailable: no published entries in the selected range.\nSource: ' + source + '\n', None
    header = [title, 'Source: ' + source, 'Range: ' + clean(old) + ' < release <= ' + clean(new)]
    if boundaries != {'old', 'new'}:
        header.append('PARTIAL: source does not show both range boundaries. Older or target notes may be missing.')
    header.append('Published entries only. The author may omit releases; completeness is not guaranteed.')
    full = list(header)
    brief = list(header)
    for number, lines in selected:
        full.extend(['', 'Version ' + number, *(lines or ['(No description supplied.)'])])
    for number, lines in selected[:5]:
        summary = ' '.join(lines[:2]) or '(No description supplied.)'
        brief.append('  ' + number + ': ' + summary[:240] + (' ...' if len(summary) > 240 else ''))
    if len(selected) > 5:
        brief.append(f'  ... {len(selected) - 5} more published releases. Use --changelog full for all entries.')
    return '\n'.join(brief) + '\n', '\n'.join(full) + '\n'


def render(entries, mode):
    """Return terminal and full texts. none performs no lookup."""
    if mode == 'none':
        return '', ''
    display, full = [], []
    for kind, name, old, new, *_ in entries:
        brief, complete = component_notes(kind, name, old, new)
        display.append(complete if mode == 'full' and complete else brief)
        full.append(complete or brief)
    return '\n'.join(display), '\n'.join(full)


def save_notes(site, text):
    """Write a private, atomic report outside htdocs. Do not run restored code."""
    descriptor, name = tempfile.mkstemp(prefix='wp-update-changelog-', suffix='.txt', dir=site['base'] / 'logs')
    path = Path(name)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(text)
        final = path.with_suffix('.complete.txt')
        os.replace(path, final)
        return final
    except Exception:
        path.unlink(missing_ok=True)
        raise
