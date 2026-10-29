import re

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
    def __init__(self, links, redirects=None, missing=(), failing=None, pageSize=500):
        self.links = links                  # title -> titles it links to
        self.redirects = redirects or {}    # old name -> real title
        self.missing = set(missing)         # titles that are red links
        self.failing = failing or {}        # title -> exception to raise when it's asked about
        self.pageSize = pageSize
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

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'params': params, 'headers': headers, 'timeout': timeout})
        title = self.resolve(params['titles'])
        if title in self.failing:
            raise self.failing[title]

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


# main

@pytest.fixture
def noRender(monkeypatch):
    rendered = []
    monkeypatch.setattr(script.Digraph, 'render', lambda self, *args, **kwargs: rendered.append(self.source))
    return rendered


def test_main_prints_the_path_and_draws_it(wiki, noRender, capsys):
    wiki({'Black hole': ['Star'], 'Star': ['Sun'], 'Sun': []})

    code = script.main(['Black hole', 'Sun', '--no-view'])

    out = capsys.readouterr().out
    assert code == 0
    assert 'Black hole -> Star -> Sun' in out
    assert '2 click(s)' in out
    assert len(noRender) == 1


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
