from graphviz import Digraph
import requests
from bs4 import BeautifulSoup

def isAGoodLink(link):
    href = link.get('href')
    linkText = link.text
    if href.startswith('/wiki/Help') or href.startswith('/wiki/Talk') or href.startswith('/wiki/Wikipedia'):
        return False
    if href is not None and linkText and href.startswith('/wiki/') and "Content_forking" not in linkText and "Content_forking" not in href:
        return True
    return False

def recurse(href, branchesDeep):
    if branchesDeep is 0:
        return
    branchesDeep = branchesDeep -1
    url = "https://en.wikipedia.org" + href
    print(url)
    res = requests.get(url).text
    soup = BeautifulSoup(res, 'html.parser')

    counter = 0
    for link in soup.find_all('a', href=True):
        if isAGoodLink(link) and counter < branchesDeep:
            newHref = link.get('href')
            print("      link: "+link.text)
            print("      href: "+ newHref)
            print("    " + link.text + " -> " + newHref)
            dot.node(newHref, link.text)
            dot.edge(href , newHref)
            counter= counter + 1
            recurse(newHref, branchesDeep)



dot = Digraph(comment='WikiLinks')
branchesDeep = 5

startingTopic = 'Autoimmune_disease'
startingUrl = "/wiki/"+startingTopic
dot.node(startingUrl, startingTopic)

recurse(startingUrl, branchesDeep)
dot.render('output/wiki-links.gv', view=True)
