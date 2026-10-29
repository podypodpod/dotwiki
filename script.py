import argparse
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote
from graphviz import Digraph
import requests
from bs4 import BeautifulSoup

# Wikipedia asks scripts to identify themselves, the default python-requests one can get blocked
HEADERS = {'User-Agent': 'dotwiki/1.0 (https://github.com/podypodpod/dotwiki)'}
WIKI = 'https://en.wikipedia.org'
API = WIKI + '/w/api.php'

# always save next to the script, wherever it's run from
OUTPUT = Path(__file__).resolve().parent / 'output' / 'wiki-path.gv'
# where the Graphviz installer puts dot on Windows, for when it isn't on the PATH yet
GRAPHVIZ_FOLDERS = [r'C:\Program Files\Graphviz\bin', r'C:\Program Files (x86)\Graphviz\bin']

# parts of the page that aren't the main article: notes at the top, nav boxes, sidebars, references
NOT_CONTENT = ['hatnote', 'navbox', 'vertical-navbox', 'sidebar', 'reflist', 'references', 'mw-editsection',
               'metadata', 'side-box', 'portalbox', 'sistersitebox']


class TooManyRequests(Exception):
    pass


class WikiApi:
    # talks to the Wikipedia API and keeps count of requests, so a search can't run forever
    def __init__(self, maxRequests):
        self.maxRequests = maxRequests
        self.requests = 0
        self.gaveUp = False

    def request(self, params):
        if self.requests >= self.maxRequests:
            self.gaveUp = True
            raise TooManyRequests(self.requests)
        self.requests = self.requests + 1
        params = {'format': 'json', 'formatversion': '2', 'redirects': '1', **params}
        res = requests.get(API, params=params, headers=HEADERS, timeout=10)
        res.raise_for_status()
        return res.json()

    def pages(self, params):
        # yields every page in the result, following "continue" until there are no more
        params = {'action': 'query', **params}
        while True:
            data = self.request(params)

            for page in data.get('query', {}).get('pages', []):
                yield page
            if 'continue' not in data:
                return
            params = {**params, **data['continue']}


def resolveTitle(api, title):
    # turns what the user typed into the real page title, following redirects. None if there's no such page
    for page in api.pages({'titles': title}):
        if page.get('missing') or page.get('invalid'):
            return None
        return page['title']
    return None

def linksFrom(api, title):
    # articles this page links to, with redirects already followed
    for page in api.pages({'generator': 'links', 'titles': title, 'gpllimit': 'max', 'gplnamespace': '0'}):
        if not page.get('missing'):
            yield page['title']

def linksTo(api, title):
    # articles that link to this page
    for page in api.pages({'generator': 'linkshere', 'titles': title, 'glhlimit': 'max', 'glhnamespace': '0', 'glhshow': '!redirect'}):
        if not page.get('missing'):
            yield page['title']

def hrefTitle(href):
    # /wiki/Black_hole#History -> Black hole, None for anything that isn't an article link
    href = href.split('#')[0]
    if href.startswith(WIKI):
        href = href[len(WIKI):]
    if not href.startswith('/wiki/'):
        return None
    title = unquote(href[len('/wiki/'):]).replace('_', ' ').strip()
    # anything with a colon is a namespace page (Help:, File:, Category:, Special: ...)
    if not title or ':' in title:
        return None
    return title[0].upper() + title[1:]

def bodyLinks(api, title):
    # titles linked from the main article text, as written on the page (so some will be redirects)
    data = api.request({'action': 'parse', 'page': title, 'prop': 'text'})
    if 'error' in data:
        raise requests.RequestException(data['error'].get('info', 'parse failed'))
    soup = BeautifulSoup(data['parse']['text'], 'html.parser')
    content = soup.select_one('.mw-parser-output') or soup
    for junk in content.find_all(class_=NOT_CONTENT):
        junk.decompose()

    links = set()
    for link in content.find_all('a', href=True):
        linked = hrefTitle(link['href'])
        if linked and link.text.strip():
            links.add(linked)
    return links

def aliases(api, title):
    # the page's own title plus every redirect to it, so a link to "Black holes" counts as "Black hole"
    names = {title}
    for page in api.pages({'titles': title, 'prop': 'redirects', 'rdlimit': 'max', 'rdnamespace': '0'}):
        for redirect in page.get('redirects', []):
            names.add(redirect['title'])
    return names

class BodyChecker:
    # remembers what it has already downloaded, so each page is only fetched once
    def __init__(self, api):
        self.api = api
        self.bodies = {}
        self.names = {}

    def inBody(self, page, nextPage):
        if page not in self.bodies:
            self.bodies[page] = bodyLinks(self.api, page)
        if nextPage not in self.names:
            self.names[nextPage] = aliases(self.api, nextPage)
        return not self.bodies[page].isdisjoint(self.names[nextPage])

def joinPath(forward, backward, meeting):
    # forward points each page back towards the start, backward points each page on towards the end
    path = []
    page = meeting
    while page is not None:
        path.append(page)
        page = forward[page]
    path.reverse()
    page = backward[meeting]
    while page is not None:
        path.append(page)
        page = backward[page]
    return path

def shortestPath(api, start, end, banned=frozenset()):
    # searches out from both ends a level at a time, always growing the smaller side, until they meet.
    # banned is a set of (page, linked page) steps to ignore
    if start == end:
        return [start], []

    forward = {start: None}
    backward = {end: None}
    forwardFrontier = [start]
    backwardFrontier = [end]
    failed = []

    try:
        while forwardFrontier and backwardFrontier:
            searchForward = len(forwardFrontier) <= len(backwardFrontier)
            if searchForward:
                frontier, seen, other, getLinks = forwardFrontier, forward, backward, linksFrom
                print("searching forward from " + str(len(frontier)) + " page(s)")
            else:
                frontier, seen, other, getLinks = backwardFrontier, backward, forward, linksTo
                print("searching backward from " + str(len(frontier)) + " page(s)")

            nextFrontier = []
            for page in frontier:
                # one bad page shouldn't stop the whole search, note it and carry on with the rest
                try:
                    for linked in getLinks(api, page):
                        step = (page, linked) if searchForward else (linked, page)
                        if linked in seen or step in banned:
                            continue
                        seen[linked] = page
                        nextFrontier.append(linked)
                        if linked in other:
                            return joinPath(forward, backward, linked), failed
                except requests.RequestException as e:
                    print("    failed: " + page + " - " + str(e))
                    failed.append((page, str(e)))

            if searchForward:
                forwardFrontier = nextFrontier
            else:
                backwardFrontier = nextFrontier
    except TooManyRequests:
        pass

    return None, failed

def articlePath(api, start, end):
    # the API counts every link on a page, nav boxes included. Find the shortest path, check each
    # step is in the main article, and if not ban that step and search again. Banned steps really
    # aren't in the article, so what's left is still the shortest path using article links only
    checker = BodyChecker(api)
    banned = set()
    failed = {}

    try:
        while True:
            path, searchFailed = shortestPath(api, start, end, banned)
            failed.update(searchFailed)
            if path is None:
                return None, list(failed.items())

            badSteps = []
            for page, nextPage in zip(path, path[1:]):
                try:
                    if not checker.inBody(page, nextPage):
                        badSteps.append((page, nextPage))
                except requests.RequestException as e:
                    # can't check it, so don't trust it
                    print("    failed: couldn't check " + page + " - " + str(e))
                    failed[page] = str(e)
                    badSteps.append((page, nextPage))

            if not badSteps:
                return path, list(failed.items())
            for page, nextPage in badSteps:
                print("    " + page + " -> " + nextPage + " isn't in the main article, searching again")
            banned.update(badSteps)
    except TooManyRequests:
        return None, list(failed.items())

def drawPath(path):
    dot = Digraph(comment='WikiPath')
    for page in path:
        dot.node(page, page)
    for page, nextPage in zip(path, path[1:]):
        dot.edge(page, nextPage)
    return dot

def findGraphviz():
    # True if dot can be run, adding the usual install folder to the PATH if that's where it is
    if shutil.which('dot'):
        return True
    for folder in GRAPHVIZ_FOLDERS:
        if os.path.isfile(os.path.join(folder, 'dot.exe')):
            os.environ['PATH'] = os.environ.get('PATH', '') + os.pathsep + folder
            return True
    return False

def parseArgs(argv=None):
    parser = argparse.ArgumentParser(description='Find the shortest chain of Wikipedia links from one page to another')
    parser.add_argument('start', help='page to start from, e.g. "Black hole"')
    parser.add_argument('end', help='page to get to, e.g. Philosophy')
    parser.add_argument('--max-requests', type=int, default=500, help='give up after this many requests to Wikipedia')
    parser.add_argument('--no-view', action='store_true', help="don't open the diagram when done")
    return parser.parse_args(argv)

def main(argv=None):
    args = parseArgs(argv)
    api = WikiApi(args.max_requests)

    try:
        start = resolveTitle(api, args.start)
        end = resolveTitle(api, args.end)
    except requests.RequestException as e:
        print("couldn't reach Wikipedia: " + str(e))
        return 1
    for typed, title in [(args.start, start), (args.end, end)]:
        if title is None:
            print("there's no Wikipedia page called " + typed)
            return 1

    path, failed = articlePath(api, start, end)

    if failed:
        print("\n" + str(len(failed)) + " page(s) couldn't be fetched, a shorter path through them might have been missed:")
        for page, error in failed:
            print("    " + page + " - " + error)

    if path is None:
        if api.gaveUp:
            print("\ngave up after " + str(api.requests) + " requests without finding a path, try a bigger --max-requests")
        else:
            print("\nno path from " + start + " to " + end)
        return 1

    clicks = len(path) - 1
    print("\n" + " -> ".join(path))
    print(str(clicks) + " click(s), found with " + str(api.requests) + " requests")
    dot = drawPath(path)
    if findGraphviz():
        dot.render(str(OUTPUT), view=not args.no_view)
        print("diagram saved to " + str(OUTPUT) + ".pdf")
    else:
        dot.save(str(OUTPUT))
        print("Graphviz isn't installed or isn't on your PATH, so only the dot file was saved to " + str(OUTPUT))
        print("install it from https://www.graphviz.org/download/ to get the pdf")
    return 0


if __name__ == '__main__':
    sys.exit(main())
