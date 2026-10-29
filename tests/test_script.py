import os
import re
from pathlib import Path
from urllib.parse import quote

import pytest
import requests

import script


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeWikipedia:
    # a tiny made-up Wikipedia that answers the same API queries the script makes
    def __init__(self, links, redirects=None, missing=(), failing=None, pageSize=500,
                 body=None, html=None, unparseable=()):
        self.links = links                  # title -> titles it links to
        self.redirects = redirects or {}    # old name -> real title
        self.missing = set(missing)         # titles that are red links
        self.failing = failing or {}        # title -> exception to raise when it's asked about
        self.pageSize = pageSize
        self.body = body or {}              # title -> links in the main article, the rest are in a nav box
        self.html = html or {}              # title -> article html, to test the html handling directly
        self.unparseable = set(unparseable) # titles whose article text can't be fetched
        self.calls = []

    def resolve(self, title):
        return self.redirects.get(title, title)

    def exists(self, title):
        return title not in self.missing and title not in self.redirects

    def page(self, title):
        return {'title': title} if self.exists(title) else {'title': title, 'missing': True}

    def results(self, titles, offset, continueKey):
        end = offset + self.pageSize
        data = {'query': {'pages': [self.page(t) for t in titles[offset:end]]}}
        if end < len(titles):
            data['continue'] = {continueKey: str(end), 'continue': '||'}
        return data

    def articleHtml(self, title):
        if title in self.html:
            return self.html[title]
        inBody = self.body.get(title, self.links.get(title, []))
        inNav = [t for t in self.links.get(title, []) if t not in inBody]

        def anchors(titles):
            return ''.join(f'<a href="/wiki/{quote(t.replace(" ", "_"))}">{t}</a> ' for t in titles)

        return (f'<div class="mw-parser-output"><p>{anchors(inBody)}</p>'
                f'<div class="navbox">{anchors(inNav)}</div></div>')

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'params': params, 'headers': headers, 'timeout': timeout})
        title = self.resolve(params.get('titles') or params.get('page'))
        if title in self.failing:
            raise self.failing[title]

        if params.get('action') == 'parse':
            if title in self.unparseable:
                return FakeResponse({'error': {'info': "The page you specified doesn't exist."}})
            return FakeResponse({'parse': {'title': title, 'text': self.articleHtml(title)}})

        if params.get('prop') == 'redirects':
            names = [{'title': old} for old, new in self.redirects.items() if new == title]
            return FakeResponse({'query': {'pages': [{'title': title, 'redirects': names}]}})

        if params.get('generator') == 'links':
            targets = list(dict.fromkeys(self.resolve(t) for t in self.links.get(title, [])))
            return FakeResponse(self.results(targets, int(params.get('gplcontinue', 0)), 'gplcontinue'))

        if params.get('generator') == 'linkshere':
            linkers = [page for page, out in self.links.items()
                       if title in (self.resolve(t) for t in out) and page not in self.redirects]
            return FakeResponse(self.results(linkers, int(params.get('glhcontinue', 0)), 'glhcontinue'))

        known = title in self.links or any(title in out for out in self.links.values())
        return FakeResponse({'query': {'pages': [self.page(title) if known else {'title': title, 'missing': True}]}})

    def generators(self):
        return [c['params'].get('generator') for c in self.calls]

    def parsed(self):
        return [c['params']['page'] for c in self.calls if c['params'].get('action') == 'parse']


@pytest.fixture
def wiki(monkeypatch):
    # call wiki(links, ...) to set up the fake Wikipedia for a test
    def make(links, **kwargs):
        fake = FakeWikipedia(links, **kwargs)
        monkeypatch.setattr(script.requests, 'get', fake.get)
        return fake
    return make


def api(maxRequests=500):
    return script.WikiApi(maxRequests)


# WikiApi

def test_requests_identify_themselves_and_ask_for_json(wiki):
    fake = wiki({'A': ['B']})

    list(script.linksFrom(api(), 'A'))

    call = fake.calls[0]
    assert call['url'] == 'https://en.wikipedia.org/w/api.php'
    assert 'dotwiki' in call['headers']['User-Agent']
    assert call['timeout'] == 10
    assert call['params']['format'] == 'json'
    assert call['params']['redirects'] == '1'


def test_api_gives_up_after_max_requests(wiki):
    wiki({'A': ['B', 'C', 'D', 'E']}, pageSize=1)
    limited = api(maxRequests=2)

    with pytest.raises(script.TooManyRequests):
        list(script.linksFrom(limited, 'A'))
    assert limited.requests == 2
    assert limited.gaveUp


# resolveTitle

def test_resolveTitle_keeps_real_titles(wiki):
    wiki({'Black hole': ['Star']})
    assert script.resolveTitle(api(), 'Black hole') == 'Black hole'


def test_resolveTitle_follows_redirects(wiki):
    wiki({'Black hole': ['Star']}, redirects={'Black holes': 'Black hole'})
    assert script.resolveTitle(api(), 'Black holes') == 'Black hole'


def test_resolveTitle_returns_none_for_missing_pages(wiki):
    wiki({'Black hole': ['Star']})
    assert script.resolveTitle(api(), 'Not a real page') is None


# linksFrom / linksTo

def test_linksFrom_follows_continue_to_get_every_link(wiki):
    fake = wiki({'A': ['B', 'C', 'D', 'E', 'F']}, pageSize=2)

    assert list(script.linksFrom(api(), 'A')) == ['B', 'C', 'D', 'E', 'F']
    assert len(fake.calls) == 3


def test_linksFrom_follows_redirects_and_skips_red_links(wiki):
    wiki({'A': ['Old name', 'Red link', 'C']}, redirects={'Old name': 'B'}, missing={'Red link'})

    assert list(script.linksFrom(api(), 'A')) == ['B', 'C']


def test_linksTo_finds_pages_linking_in(wiki):
    wiki({'A': ['Target'], 'B': ['Other'], 'C': ['Old name']}, redirects={'Old name': 'Target'})

    assert list(script.linksTo(api(), 'Target')) == ['A', 'C']


# hrefTitle / bodyLinks / aliases

@pytest.mark.parametrize('href, expected', [
    ('/wiki/Black_hole', 'Black hole'),
    ('/wiki/Black_hole#History', 'Black hole'),
    ('https://en.wikipedia.org/wiki/Black_hole', 'Black hole'),
    ('/wiki/Caf%C3%A9', 'Café'),
    ('/wiki/iPhone', 'IPhone'),
    ('/wiki/File:Picture.jpg', None),
    ('/wiki/Category:Physics', None),
    ('#cite_note-1', None),
    ('/w/index.php?title=Black_hole&action=edit', None),
    ('https://example.com/wiki/Black_hole', None),
])
def test_hrefTitle(href, expected):
    assert script.hrefTitle(href) == expected


def test_bodyLinks_only_keeps_the_main_article(wiki):
    wiki({}, html={'Start': '''
        <div class="mw-parser-output">
          <div class="hatnote"><a href="/wiki/Start_(disambiguation)">other uses</a></div>
          <table class="sidebar"><tr><td><a href="/wiki/Sidebar_link">sidebar</a></td></tr></table>
          <table class="infobox"><tr><td><a href="/wiki/Infobox_link">infobox</a></td></tr></table>
          <p>Text with <a href="/wiki/First">first</a>, <a href="/wiki/File:Pic.jpg">a picture</a>,
             <a href="#cite_note-1">[1]</a>, <a href="/wiki/Second#History">second</a>
             and <a href="/wiki/Empty"> </a>.</p>
          <div class="reflist"><a href="/wiki/Reference_link">reference</a></div>
          <div class="navbox"><a href="/wiki/Navbox_link">navbox</a></div>
        </div>'''})

    assert script.bodyLinks(api(), 'Start') == {'First', 'Second', 'Infobox link'}


def test_bodyLinks_raises_when_the_article_cant_be_fetched(wiki):
    wiki({'Start': ['A']}, unparseable={'Start'})

    with pytest.raises(requests.RequestException):
        script.bodyLinks(api(), 'Start')


def test_aliases_includes_redirects(wiki):
    wiki({'Black hole': []}, redirects={'Black holes': 'Black hole', 'Blackhole': 'Black hole', 'Other': 'X'})

    assert script.aliases(api(), 'Black hole') == {'Black hole', 'Black holes', 'Blackhole'}


def test_BodyChecker_counts_links_through_redirects(wiki):
    wiki({'A': ['Black holes', 'Nav']}, body={'A': ['Black holes']}, redirects={'Black holes': 'Black hole'})
    checker = script.BodyChecker(api())

    assert checker.inBody('A', 'Black hole')
    assert not checker.inBody('A', 'Nav')


def test_BodyChecker_fetches_each_article_once(wiki):
    fake = wiki({'A': ['B', 'C']})
    checker = script.BodyChecker(api())

    checker.inBody('A', 'B')
    checker.inBody('A', 'C')

    assert fake.parsed() == ['A']


# joinPath / drawPath

def test_joinPath_joins_both_halves():
    forward = {'Start': None, 'A': 'Start', 'Meet': 'A'}
    backward = {'End': None, 'B': 'End', 'Meet': 'B'}

    assert script.joinPath(forward, backward, 'Meet') == ['Start', 'A', 'Meet', 'B', 'End']


def test_drawPath_draws_a_chain():
    dot = script.drawPath(['Start', 'Middle', 'End'])

    assert re.findall(r'"?([\w ]+)"? -> "?([\w ]+)"?', dot.source) == [('Start', 'Middle'), ('Middle', 'End')]


# shortestPath

def test_same_start_and_end_needs_no_requests(wiki):
    fake = wiki({'A': ['B']})

    assert script.shortestPath(api(), 'A', 'A') == (['A'], [])
    assert fake.calls == []


def test_finds_a_direct_link(wiki):
    wiki({'A': ['X', 'B'], 'X': []})

    path, failed = script.shortestPath(api(), 'A', 'B')

    assert path == ['A', 'B']
    assert failed == []


def test_finds_a_longer_chain(wiki):
    wiki({'A': ['B'], 'B': ['C'], 'C': ['D'], 'D': ['E'], 'E': []})

    path, _ = script.shortestPath(api(), 'A', 'E')

    assert path == ['A', 'B', 'C', 'D', 'E']


def test_picks_the_shortest_of_several_paths(wiki):
    wiki({
        'A': ['Long1', 'Short'],
        'Long1': ['Long2'],
        'Long2': ['Long3'],
        'Long3': ['End'],
        'Short': ['End'],
    })

    path, _ = script.shortestPath(api(), 'A', 'End')

    assert path == ['A', 'Short', 'End']


def test_searches_backward_when_that_side_is_smaller(wiki):
    # the start links to lots of pages, but only one page links to the end
    links = {'Start': ['P' + str(i) for i in range(20)]}
    for i in range(20):
        links['P' + str(i)] = []
    links['P7'] = ['Bridge']
    links['Bridge'] = ['End']
    fake = wiki(links)

    path, _ = script.shortestPath(api(), 'Start', 'End')

    assert path == ['Start', 'P7', 'Bridge', 'End']
    assert 'linkshere' in fake.generators()
    # it shouldn't have needed to look at every one of the 20 pages' links
    assert fake.generators().count('links') < 20


def test_follows_redirects_along_the_way(wiki):
    wiki({'A': ['Old name'], 'B': ['End']}, redirects={'Old name': 'B'})

    path, _ = script.shortestPath(api(), 'A', 'End')

    assert path == ['A', 'B', 'End']


def test_returns_none_when_there_is_no_path(wiki):
    wiki({'A': ['B'], 'B': ['A'], 'C': ['End']})
    limited = api()

    path, failed = script.shortestPath(limited, 'A', 'End')

    assert path is None
    assert failed == []
    assert not limited.gaveUp


def test_reports_a_failed_page_and_still_finds_a_path_round_it(wiki):
    wiki({'A': ['Broken', 'C'], 'C': ['D'], 'D': ['End'], 'Broken': ['X'], 'X': ['End']},
         failing={'Broken': requests.ConnectionError('connection refused')})

    path, failed = script.shortestPath(api(), 'A', 'End')

    assert path == ['A', 'C', 'D', 'End']
    assert failed == [('Broken', 'connection refused')]


def test_gives_up_at_the_request_limit(wiki):
    links = {'P' + str(i): ['P' + str(i + 1)] for i in range(50)}
    wiki(links)
    limited = api(maxRequests=5)

    path, _ = script.shortestPath(limited, 'P0', 'P50')

    assert path is None
    assert limited.gaveUp
    assert limited.requests == 5


def test_shortestPath_skips_banned_steps_both_ways(wiki):
    wiki({'A': ['Shortcut', 'B'], 'Shortcut': ['End'], 'B': ['C'], 'C': ['End']})

    path, _ = script.shortestPath(api(), 'A', 'End', banned={('A', 'Shortcut')})
    assert path == ['A', 'B', 'C', 'End']

    path, _ = script.shortestPath(api(), 'A', 'End', banned={('Shortcut', 'End')})
    assert path == ['A', 'B', 'C', 'End']


# articlePath

def test_articlePath_ignores_nav_box_shortcuts(wiki):
    # A only links to Shortcut from its nav box, so the real answer is the longer way round
    wiki({'A': ['Shortcut', 'B'], 'Shortcut': ['End'], 'B': ['C'], 'C': ['End']}, body={'A': ['B']})

    path, failed = script.articlePath(api(), 'A', 'End')

    assert path == ['A', 'B', 'C', 'End']
    assert failed == []


def test_articlePath_keeps_a_path_that_is_all_article_links(wiki):
    fake = wiki({'A': ['B'], 'B': ['End']})

    path, _ = script.articlePath(api(), 'A', 'End')

    assert path == ['A', 'B', 'End']
    # it only searched once, then checked the two steps
    assert sorted(fake.parsed()) == ['A', 'B']


def test_articlePath_keeps_going_through_several_nav_box_links(wiki):
    wiki({
        'A': ['Nav1', 'Nav2', 'B'],
        'Nav1': ['End'],
        'Nav2': ['End'],
        'B': ['Nav3', 'C'],
        'Nav3': ['End'],
        'C': ['End'],
    }, body={'A': ['B'], 'B': ['C']})

    path, _ = script.articlePath(api(), 'A', 'End')

    assert path == ['A', 'B', 'C', 'End']


def test_articlePath_accepts_article_links_through_redirects(wiki):
    wiki({'A': ['Old name'], 'B': ['End']}, redirects={'Old name': 'B'})

    path, _ = script.articlePath(api(), 'A', 'End')

    assert path == ['A', 'B', 'End']


def test_articlePath_reports_a_step_it_couldnt_check(wiki):
    wiki({'A': ['Shortcut', 'B'], 'Shortcut': ['End'], 'B': ['C'], 'C': ['End']}, unparseable={'Shortcut'})

    path, failed = script.articlePath(api(), 'A', 'End')

    assert path == ['A', 'B', 'C', 'End']
    assert [page for page, _ in failed] == ['Shortcut']


def test_articlePath_returns_none_when_only_nav_boxes_connect(wiki):
    wiki({'A': ['End']}, body={'A': []})

    path, _ = script.articlePath(api(), 'A', 'End')

    assert path is None


def test_articlePath_gives_up_at_the_request_limit(wiki):
    links = {'P' + str(i): ['P' + str(i + 1), 'Nav'] for i in range(10)}
    links['Nav'] = ['P10']
    body = {'P' + str(i): ['P' + str(i + 1)] for i in range(10)}
    limited = api(maxRequests=8)
    wiki(links, body=body)

    path, _ = script.articlePath(limited, 'P0', 'P10')

    assert path is None
    assert limited.gaveUp


# main

@pytest.fixture
def noRender(monkeypatch):
    # records where diagrams would have been rendered, without needing Graphviz
    rendered = []
    monkeypatch.setattr(script, 'findGraphviz', lambda: True)
    monkeypatch.setattr(script.Digraph, 'render', lambda self, filename, **kwargs: rendered.append(filename))
    return rendered


@pytest.fixture
def noGraphviz(monkeypatch):
    saved = []
    monkeypatch.setattr(script, 'findGraphviz', lambda: False)
    monkeypatch.setattr(script.Digraph, 'render', lambda self, *args, **kwargs: pytest.fail('tried to render'))
    monkeypatch.setattr(script.Digraph, 'save', lambda self, filename, **kwargs: saved.append(filename))
    return saved


def test_main_prints_the_path_and_draws_it(wiki, noRender, capsys):
    wiki({'Black hole': ['Star'], 'Star': ['Sun'], 'Sun': []})

    code = script.main(['Black hole', 'Sun', '--no-view'])

    out = capsys.readouterr().out
    assert code == 0
    assert 'Black hole -> Star -> Sun' in out
    assert '2 click(s)' in out
    assert len(noRender) == 1


def test_main_saves_next_to_the_script_wherever_its_run_from(wiki, noRender, monkeypatch, tmp_path):
    wiki({'A': ['B'], 'B': []})
    monkeypatch.chdir(tmp_path)

    script.main(['A', 'B', '--no-view'])

    expected = str(Path(script.__file__).resolve().parent / 'output' / 'wiki-path.gv')
    assert noRender == [expected]


def test_main_saves_the_dot_file_when_graphviz_is_missing(wiki, noGraphviz, capsys):
    wiki({'A': ['B'], 'B': []})

    code = script.main(['A', 'B', '--no-view'])

    out = capsys.readouterr().out
    assert code == 0
    assert 'A -> B' in out
    assert "Graphviz isn't installed" in out
    assert noGraphviz == [str(script.OUTPUT)]


def test_main_only_uses_article_links(wiki, noRender, capsys):
    wiki({'A': ['Shortcut', 'B'], 'Shortcut': ['End'], 'B': ['End']}, body={'A': ['B']})

    code = script.main(['A', 'End', '--no-view'])

    out = capsys.readouterr().out
    assert code == 0
    assert 'A -> Shortcut' in out and "isn't in the main article" in out
    assert 'A -> B -> End' in out


def test_main_says_when_a_page_does_not_exist(wiki, noRender, capsys):
    wiki({'Black hole': ['Star']})

    code = script.main(['Black hole', 'Not a real page', '--no-view'])

    assert code == 1
    assert "no Wikipedia page called Not a real page" in capsys.readouterr().out
    assert noRender == []


def test_main_says_when_it_gives_up(wiki, noRender, capsys):
    wiki({'P' + str(i): ['P' + str(i + 1)] for i in range(50)})

    code = script.main(['P0', 'P50', '--max-requests', '6', '--no-view'])

    assert code == 1
    assert 'gave up after 6 requests' in capsys.readouterr().out


def test_main_says_when_there_is_no_path(wiki, noRender, capsys):
    wiki({'A': ['B'], 'B': ['A'], 'C': ['End']})

    code = script.main(['A', 'End', '--no-view'])

    assert code == 1
    assert 'no path from A to End' in capsys.readouterr().out


# findGraphviz

def test_findGraphviz_uses_dot_on_the_path(monkeypatch):
    monkeypatch.setattr(script.shutil, 'which', lambda name: r'C:\somewhere\dot.exe')
    assert script.findGraphviz()


def test_findGraphviz_finds_the_usual_install_folder(monkeypatch, tmp_path):
    (tmp_path / 'dot.exe').write_text('')
    monkeypatch.setattr(script.shutil, 'which', lambda name: None)
    monkeypatch.setattr(script, 'GRAPHVIZ_FOLDERS', [str(tmp_path / 'not here'), str(tmp_path)])
    monkeypatch.setenv('PATH', 'C:\\Windows')

    assert script.findGraphviz()
    assert os.environ['PATH'].endswith(os.pathsep + str(tmp_path))


def test_findGraphviz_gives_up_when_its_nowhere(monkeypatch, tmp_path):
    monkeypatch.setattr(script.shutil, 'which', lambda name: None)
    monkeypatch.setattr(script, 'GRAPHVIZ_FOLDERS', [str(tmp_path)])

    assert not script.findGraphviz()


# parseArgs

def test_parseArgs_defaults():
    args = script.parseArgs(['Black hole', 'Philosophy'])
    assert args.start == 'Black hole'
    assert args.end == 'Philosophy'
    assert args.max_requests == 500
    assert args.no_view is False


def test_parseArgs_needs_a_start_and_an_end():
    with pytest.raises(SystemExit):
        script.parseArgs(['Black hole'])
