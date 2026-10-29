import argparse
import sys
from graphviz import Digraph
import requests

# Wikipedia asks scripts to identify themselves, the default python-requests one can get blocked
HEADERS = {'User-Agent': 'dotwiki/1.0 (https://github.com/podypodpod/dotwiki)'}
WIKI = 'https://en.wikipedia.org'
API = WIKI + '/w/api.php'


class TooManyRequests(Exception):
    pass


class WikiApi:
    # talks to the Wikipedia API and keeps count of requests, so a search can't run forever
    def __init__(self, maxRequests):
        self.maxRequests = maxRequests
        self.requests = 0
        self.gaveUp = False

    def pages(self, params):
        # yields every page in the result, following "continue" until there are no more
        params = {'action': 'query', 'format': 'json', 'formatversion': '2', 'redirects': '1', **params}
        while True:
            if self.requests >= self.maxRequests:
                self.gaveUp = True
                raise TooManyRequests(self.requests)
            self.requests = self.requests + 1
            res = requests.get(API, params=params, headers=HEADERS, timeout=10)
            res.raise_for_status()
            data = res.json()

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

def shortestPath(api, start, end):
    # searches out from both ends a level at a time, always growing the smaller side, until they meet
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
                        if linked in seen:
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

def drawPath(path):
    dot = Digraph(comment='WikiPath')
    for page in path:
        dot.node(page, page)
    for page, nextPage in zip(path, path[1:]):
        dot.edge(page, nextPage)
    return dot

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

    path, failed = shortestPath(api, start, end)

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
    drawPath(path).render('output/wiki-path.gv', view=not args.no_view)
    return 0


if __name__ == '__main__':
    sys.exit(main())
