# DotWiki

## Description
Creates a dot notation graph of the links between Wikipedia pages.

It starts at one page and follows links from the article text a few levels deep: 4 links from the starting page, then 3 from each of those, then 2, then 1. Links to the same page twice, disambiguation pages and namespace pages (`File:`, `Category:`, `Help:` ...) are skipped.

[Wikipedia DOT graph notation](https://en.wikipedia.org/wiki/DOT_(graph_description_language))

[Good place to view and edit raw dot notation](https://dreampuf.github.io/GraphvizOnline/)

## Setup

### Graphviz
Download and install Graphviz from https://www.graphviz.org/download/ (or `winget install Graphviz.Graphviz` on Windows), then add its `bin` folder to your PATH. Check it works with `dot -V`.

### Python dependencies
Needs Python 3.9 or newer.

```
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

## Usage

```
python script.py                          # starts at Autoimmune_disease, 5 levels deep
python script.py "Black hole"             # pick a starting page
python script.py "Black hole" --depth 3   # fewer levels, fewer requests
python script.py --no-view                # don't open the diagram when done
```

Depth 5 fetches up to 65 pages, so bigger depths get slow quickly.

### Output
See the `output` folder for `wiki-links.gv`, the raw dot notation graph, and `wiki-links.gv.pdf`, the viewable diagram.
