PY ?= python3
PUBLIC_CSV := docs/output/meatmap.csv
CURRENT_CSV := output/meatmap.hotpepper.csv

.PHONY: csv local-serve pages-serve test validate check

csv:
	@set -eu; \
	public_csv="$(PUBLIC_CSV)"; \
	current_csv="$(CURRENT_CSV)"; \
	test -f "$$public_csv" || { \
		echo "ERROR: $$public_csv がありません。旧データを失わないため更新を中止します。" >&2; \
		exit 1; \
	}; \
	mkdir -p output; \
	backup_csv="$$(mktemp output/meatmap.previous.XXXXXX.csv)"; \
	rollback_needed=0; \
	cleanup() { \
		status=$$?; \
		if [ "$$rollback_needed" -eq 1 ]; then \
			mv -f "$$backup_csv" "$$public_csv"; \
			echo "ERROR: 更新に失敗したため $$public_csv を更新前の内容へ戻しました。" >&2; \
		fi; \
		rm -f "$$current_csv" "$$backup_csv"; \
		trap - EXIT; \
		exit "$$status"; \
	}; \
	trap cleanup EXIT; \
	cp -p "$$public_csv" "$$backup_csv"; \
	$(PY) -m meatmap.cli \
		--output "$$current_csv" \
		--include-rank-b \
		--include-rank-c; \
	rollback_needed=1; \
	$(PY) scripts/merge_public_dataset.py \
		--current "$$current_csv" \
		--legacy "$$public_csv" \
		--output "$$public_csv"; \
	chmod 0644 "$$public_csv"; \
	$(PY) scripts/validate_public_site.py; \
	rollback_needed=0; \
	echo "Updated and validated $$public_csv"

local-serve:
	$(PY) -m http.server 8000 --bind 127.0.0.1 --directory docs

pages-serve:
	$(PY) -m http.server 8000 --bind 127.0.0.1 --directory docs

test:
	$(PY) -m pytest -q

validate:
	$(PY) scripts/validate_public_site.py

check: test validate
