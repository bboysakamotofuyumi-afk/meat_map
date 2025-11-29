# Tokyo Carnivore Meat Map

## ローカル実行と公開までの最短手順

1. 環境変数/鍵の準備  
   - `HOTPEPPER_API_KEY` を `.env` か環境変数でセット。
2. CSV生成（S/Aのみデフォルト）  
   - `python -m meatmap.cli --output output/meatmap.csv --copy-to-docs`  
   - Pages用に `docs/output/meatmap.csv` にもコピーされる。
3. ローカルでマップ確認  
   - `python -m http.server 8000`  
   - `http://localhost:8000/docs/map_demo.html?csv=output/meatmap.csv` を開く。
4. 公開（GitHub Pages）  
   - `.github/workflows/deploy-pages.yml` を有効にし、PagesのSourceを「GitHub Actions」に設定。  
   - 公開URL例: `https://genkishimura2000.github.io/meat_map/map_demo.html?csv=output/meatmap.csv`

## データ更新フロー
- 取得&コピー: `python -m meatmap.cli --output output/meatmap.csv --copy-to-docs`
- コミット/プッシュ: `git add docs/output/meatmap.csv` など。Pagesワークフローが自動デプロイ。

## 注意
- APIキーやPATはリポジトリに含めない。使い終わったPATはRevoke推奨。***
