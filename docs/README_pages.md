# GitHub Pages でマップデモを公開する手順

このリポジトリは `docs/` を Pages 配信ディレクトリとして想定しています。CSV を `docs/output/meatmap.csv` にコピーし、`docs/map_demo.html` から参照します。GitHub Actions で `docs/` を gh-pages にデプロイするワークフローも同梱しています。

## 前提
- GitHub Pages の設定: Branch を作業ブランチ（例: `make-a-big-promise` または `main`）にし、Folder を `/docs` に指定。
- `HOTPEPPER_API_KEY` を `.env` などでセット済み（公開しない）。

## 毎回の流れ
1. データ生成とコピー  
   `python -m meatmap.cli --output output/meatmap.csv --copy-to-docs`
   - `--copy-to-docs` で `docs/output/meatmap.csv` に配置され、Pages から参照できる。
2. 動作確認（ローカル）  
   `python -m http.server 8000` をリポジトリルートで起動し、  
   `http://localhost:8000/docs/map_demo.html?csv=output/meatmap.csv` を開く。
3. コミット & プッシュ  
   `docs/output/meatmap.csv` を含めてコミットし、GitHub へプッシュ。
4. 公開URL（GitHub Actions / gh-pages 利用時）  
   `https://<GitHubユーザー名>.github.io/meat_map/map_demo.html?csv=output/meatmap.csv`
   - Pages 設定は「Source: GitHub Actions」を選び、`.github/workflows/deploy-pages.yml` を使用。
   - ブランチデプロイで `/docs` を使う場合は従来どおり `/docs/` プレフィックスでアクセス。

## メモ
- `.env` や APIキーはコミットしない（`--copy-to-docs` は CSV だけコピーする）。
- レスポンス件数が多い場合でもページサイズは調整済み（HotPepper はジャンル30件/キーワード20件単位で取得）。***
