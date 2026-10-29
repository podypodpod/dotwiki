# DotWiki

## Description
Finds the shortest chain of links from one Wikipedia page to another (the [Wikipedia game](https://en.wikipedia.org/wiki/Wikipedia:Wiki_Game), or Wikiracing), and draws it as a dot notation graph.

```
Black hole -> David Hilbert -> Philosophy
2 click(s), found with 5 requests
```

It uses the Wikipedia API to search out from both ends at once: forwards through the links on the start page, and backwards through the pages that link to the end page. Each round it grows whichever side is smaller, and stops as soon as the two sides meet. That gives the shortest path while usually only needing a handful of requests. Redirects are followed, so `"Black holes"` works the same as `"Black hole"`.

Links count if they're anywhere on the page, including the nav boxes at the bottom, the same as clicking them yourself.

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
python script.py "Black hole" Philosophy
python script.py "Kevin Bacon" "Ancient Rome" --no-view    # don't open the diagram when done
python script.py Cheese Volcano --max-requests 2000        # allow a longer search
```

It gives up after 500 requests by default. If a page fails to load it carries on without it, and lists it at the end in case a shorter path went through it. It exits with 1 if no path was found.

### Output
See the `output` folder for `wiki-path.gv`, the raw dot notation graph, and `wiki-path.gv.pdf`, the viewable diagram.

## Tests
The tests use a made-up Wikipedia instead of the real one, so they don't need a connection.

```
pip install -r requirements-dev.txt
pytest
```
