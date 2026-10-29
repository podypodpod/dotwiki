import re

import pytest
import requests
from bs4 import BeautifulSoup

import script


def page(body):
    # roughly how a Wikipedia article is laid out, with a featured badge before the article itself
    return f'''
    <html><body>
      <div class="mw-indicator"><div class="mw-parser-output">
        <a href="/wiki/Wikipedia:Featured_articles">featured</a>
      </div></div>
      <div id="mw-content-text"><div class="mw-parser-output">{body}</div></div>
    </body></html>'''


def links(*titles):
    return page(''.join(f'<p><a href="https://en.wikipedia.org/wiki/{t}">{t}</a></p>' for t in titles))


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(str(self.status))


@pytest.fixture
def fakeWiki(monkeypatch):
    # pages maps a /wiki/ path to its html, calls records every request made
    pages = {}
    calls = []

    def fakeGet(url, headers=None, timeout=None):
        calls.append({'url': url, 'headers': headers, 'timeout': timeout})
        path = url[len(script.WIKI):]
        if path not in pages:
            return FakeResponse('', 404)
        return FakeResponse(pages[path])

    monkeypatch.setattr(script.requests, 'get', fakeGet)
    return pages, calls


def anchor(href, text='link'):
    return BeautifulSoup(f'<a href="{href}">{text}</a>', 'html.parser').a


def edges(dot):
    return re.findall(r'"([^"]+)" -> "([^"]+)"', dot.source)


def nodeIds(dot):
    return re.findall(r'^\s*"([^"]+)" \[label=', dot.source, re.MULTILINE)


# wikiPath

@pytest.mark.parametrize('href, expected', [
    ('/wiki/Lupus', '/wiki/Lupus'),
    ('https://en.wikipedia.org/wiki/Lupus', '/wiki/Lupus'),
    ('https://en.wikipedia.org/wiki/Lupus#Signs', '/wiki/Lupus'),
    ('/wiki/Lupus#Signs', '/wiki/Lupus'),
    ('#cite_note-1', ''),
    ('https://example.com/wiki/Lupus', 'https://example.com/wiki/Lupus'),
])
def test_wikiPath(href, expected):
    assert script.wikiPath(href) == expected


# isAGoodLink

@pytest.mark.parametrize('href', [
    '/wiki/Lupus',
    'https://en.wikipedia.org/wiki/Malar_rash',
    '/wiki/Specialty_(medicine)',
])
def test_isAGoodLink_accepts_articles(href):
    assert script.isAGoodLink(anchor(href))


@pytest.mark.parametrize('href', [
    '/wiki/File:Lupusfoto.jpg',
    '/wiki/Category:Autoimmune_diseases',
    '/wiki/Help:IPA',
    '/wiki/Talk:Lupus',
    '/wiki/Wikipedia:Featured_articles',
    '/wiki/MOS:MED',
    '/wiki/Special:Random',
    '/wiki/Lupus_(disambiguation)',
    '/wiki/Main_Page',
    '#cite_note-1',
    '/w/index.php?title=Lupus&action=edit',
    'https://example.com/wiki/Lupus',
])
def test_isAGoodLink_rejects_non_articles(href):
    assert not script.isAGoodLink(anchor(href))


def test_isAGoodLink_rejects_links_without_text():
    assert not script.isAGoodLink(anchor('/wiki/Lupus', text='   '))


# articleLinks

def test_articleLinks_skips_badges_and_non_article_parts(fakeWiki):
    pages, _ = fakeWiki
    pages['/wiki/Start'] = page('''
        <div class="hatnote"><a href="/wiki/Start_(disambiguation)">other uses</a>
            <a href="/wiki/Hatnote_target">hatnote target</a></div>
        <table class="sidebar"><tr><td><a href="/wiki/Sidebar_link">sidebar</a></td></tr></table>
        <p>Text with <a href="https://en.wikipedia.org/wiki/First">first</a>
           and <a href="/wiki/File:Picture.jpg">a picture</a>
           and <a href="#cite_note-1">[1]</a>
           and <a href="/wiki/Second#History">second</a>.</p>
        <div class="navbox"><a href="/wiki/Navbox_link">navbox</a></div>
        <div class="reflist"><a href="/wiki/Reference_link">reference</a></div>
    ''')

    assert script.articleLinks('/wiki/Start') == [
        ('/wiki/First', 'first'),
        ('/wiki/Second', 'second'),
    ]


def test_articleLinks_removes_duplicates_and_self_links(fakeWiki):
    pages, _ = fakeWiki
    pages['/wiki/Start'] = page('''
        <a href="/wiki/Start">this page</a>
        <a href="/wiki/Start#Section">this page again</a>
        <a href="/wiki/Other">other</a>
        <a href="https://en.wikipedia.org/wiki/Other">other again</a>
    ''')

    assert script.articleLinks('/wiki/Start') == [('/wiki/Other', 'other')]


def test_articleLinks_returns_nothing_without_article(fakeWiki):
    pages, _ = fakeWiki
    pages['/wiki/Empty'] = '<html><body><div class="mw-parser-output"><a href="/wiki/X">x</a></div></body></html>'

    assert script.articleLinks('/wiki/Empty') == []


def test_articleLinks_sends_user_agent_and_timeout(fakeWiki):
    pages, calls = fakeWiki
    pages['/wiki/Start'] = links('A')

    script.articleLinks('/wiki/Start')

    assert calls[0]['url'] == 'https://en.wikipedia.org/wiki/Start'
    assert 'dotwiki' in calls[0]['headers']['User-Agent']
    assert calls[0]['timeout'] == 10


def test_articleLinks_raises_on_http_error(fakeWiki):
    with pytest.raises(requests.HTTPError):
        script.articleLinks('/wiki/Missing')


# buildGraph

def test_buildGraph_follows_fewer_links_each_level(fakeWiki):
    pages, calls = fakeWiki
    pages['/wiki/Start'] = links('A', 'B', 'C')
    pages['/wiki/A'] = links('A1', 'A2')
    pages['/wiki/B'] = links('B1', 'B2')

    dot = script.buildGraph('Start', 3)

    # depth 3: 2 links from Start, then 1 from each of those, and the last level isn't fetched
    assert edges(dot) == [
        ('/wiki/Start', '/wiki/A'),
        ('/wiki/A', '/wiki/A1'),
        ('/wiki/Start', '/wiki/B'),
        ('/wiki/B', '/wiki/B1'),
    ]
    assert [c['url'] for c in calls] == [
        'https://en.wikipedia.org/wiki/Start',
        'https://en.wikipedia.org/wiki/A',
        'https://en.wikipedia.org/wiki/B',
    ]


def test_buildGraph_does_not_refetch_visited_pages(fakeWiki):
    pages, calls = fakeWiki
    pages['/wiki/Start'] = links('A', 'B', 'C')
    pages['/wiki/A'] = links('B', 'Start', 'C')
    pages['/wiki/B'] = links('A', 'C', 'Start')
    pages['/wiki/C'] = links('A', 'B', 'Start')

    dot = script.buildGraph('Start', 4)

    fetched = [c['url'] for c in calls]
    assert len(fetched) == len(set(fetched))
    # the links back to pages already seen are still drawn
    assert ('/wiki/A', '/wiki/B') in edges(dot)
    assert ('/wiki/B', '/wiki/A') in edges(dot)


def test_buildGraph_adds_each_node_once(fakeWiki):
    pages, _ = fakeWiki
    pages['/wiki/Start'] = links('A', 'B', 'C')
    pages['/wiki/A'] = links('C', 'B')
    pages['/wiki/B'] = links('C', 'A')
    pages['/wiki/C'] = links('A', 'B')

    dot = script.buildGraph('Start', 4)

    ids = nodeIds(dot)
    assert len(ids) == len(set(ids))
    assert set(ids) == {'/wiki/Start', '/wiki/A', '/wiki/B', '/wiki/C'}


def test_buildGraph_depth_one_makes_no_requests(fakeWiki):
    _, calls = fakeWiki

    dot = script.buildGraph('Start', 1)

    assert calls == []
    assert nodeIds(dot) == ['/wiki/Start']


def test_buildGraph_turns_spaces_into_underscores(fakeWiki):
    pages, calls = fakeWiki
    pages['/wiki/Black_hole'] = links('Star')

    dot = script.buildGraph('Black hole', 2)

    assert calls[0]['url'] == 'https://en.wikipedia.org/wiki/Black_hole'
    assert edges(dot) == [('/wiki/Black_hole', '/wiki/Star')]


# parseArgs

def test_parseArgs_defaults():
    args = script.parseArgs([])
    assert args.topic == 'Autoimmune_disease'
    assert args.depth == 5
    assert args.no_view is False


def test_parseArgs_options():
    args = script.parseArgs(['Black hole', '--depth', '3', '--no-view'])
    assert args.topic == 'Black hole'
    assert args.depth == 3
    assert args.no_view is True
