import argparse
from graphviz import Digraph
import requests
from bs4 import BeautifulSoup

# Wikipedia asks scripts to identify themselves, the default python-requests one can get blocked
HEADERS = {'User-Agent': 'dotwiki/1.0 (https://github.com/podypodpod/dotwiki)'}
WIKI = 'https://en.wikipedia.org'

# parts of the article that aren't really the article: notes at the top, nav boxes, references
NOT_CONTENT = ['hatnote', 'navbox', 'vertical-navbox', 'sidebar', 'reflist', 'references', 'mw-editsection', 'metadata']

def wikiPath(href):
    # links can be full urls or just /wiki/Page, keep only the /wiki/Page part
    href = href.split('#')[0]
    if href.startswith(WIKI):
        href = href[len(WIKI):]
    return href

def isAGoodLink(link):
    href = wikiPath(link.get('href'))
    linkText = link.text.strip()
    if not linkText or not href.startswith('/wiki/'):
        return False
    title = href[len('/wiki/'):]
    # anything with a colon is a namespace page (Help:, File:, Category:, MOS:, Special: ...)
    if ':' in title:
        return False
    if '(disambiguation)' in title or title == 'Main_Page':
        return False
    return True

def articleLinks(href):
    url = WIKI + href
    print(url)
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, 'html.parser')

    # look inside mw-content-text, the featured/protected badges at the top also use mw-parser-output
    content = soup.select_one('#mw-content-text .mw-parser-output')
    if content is None:
        return []
    for junk in content.find_all(class_=NOT_CONTENT):
        junk.decompose()

    links = []
    seen = set()
    for link in content.find_all('a', href=True):
        if isAGoodLink(link):
            newHref = wikiPath(link.get('href'))
            if newHref != href and newHref not in seen:
                seen.add(newHref)
                links.append((newHref, link.text.strip()))
    return links

def buildGraph(topic, branchesDeep):
    dot = Digraph(comment='WikiLinks')
    visited = set()

    startingTopic = topic.replace(' ', '_')
    startingUrl = "/wiki/" + startingTopic
    nodes = {startingUrl}
    dot.node(startingUrl, startingTopic)
    failed = []

    def recurse(href, branchesDeep):
        # at the last level there are no links left to follow, so don't fetch the page
        if branchesDeep <= 1 or href in visited:
            return
        visited.add(href)
        branchesDeep = branchesDeep - 1

        # one bad page shouldn't stop the whole run, note it and carry on with the rest
        try:
            pageLinks = articleLinks(href)
        except requests.RequestException as e:
            print("    failed: " + str(e))
            failed.append((href, str(e)))
            return

        for newHref, linkText in pageLinks[:branchesDeep]:
            print("    " + linkText + " -> " + newHref)
            if newHref not in nodes:
                nodes.add(newHref)
                dot.node(newHref, linkText)
            dot.edge(href, newHref)
            recurse(newHref, branchesDeep)

    recurse(startingUrl, branchesDeep)
    return dot, failed

def parseArgs(argv=None):
    parser = argparse.ArgumentParser(description='Draw a diagram of the links between Wikipedia pages')
    parser.add_argument('topic', nargs='?', default='Autoimmune_disease', help='starting page, e.g. Autoimmune_disease')
    parser.add_argument('--depth', type=int, default=5, help='how many levels of links to follow')
    parser.add_argument('--no-view', action='store_true', help="don't open the diagram when done")
    return parser.parse_args(argv)


if __name__ == '__main__':
    args = parseArgs()
    dot, failed = buildGraph(args.topic, args.depth)
    dot.render('output/wiki-links.gv', view=not args.no_view)

    if failed:
        print("\n" + str(len(failed)) + " page(s) couldn't be fetched, their links are missing from the diagram:")
        for href, error in failed:
            print("    " + href + " - " + error)
