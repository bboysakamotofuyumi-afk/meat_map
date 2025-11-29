PY ?= python3

.PHONY: csv local-serve pages-serve

csv:
	$(PY) -m meatmap.cli --output output/meatmap.csv --copy-to-docs

local-serve:
	$(PY) -m http.server 8000

pages-serve:
	$(PY) -m http.server 8000 --directory docs
