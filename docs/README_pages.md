# GitHub Pages 公開手順

このリポジトリは `main` ブランチの `docs/` を GitHub Pages で公開します。

## 初回設定

GitHub のリポジトリ設定で Pages を開き、次の値を選びます。

- Source: `Deploy from a branch`
- Branch: `main`
- Folder: `/docs`

公開URLは `https://bboysakamotofuyumi-afk.github.io/meat_map/` です。

## データ更新と確認

1. `HOTPEPPER_API_KEY` を環境変数または Git 管理対象外の `.env` に設定します。キーをコマンドライン、CSV、コミットへ書きません。
2. 統合済みの `docs/output/meatmap.csv` があることを確認します。このファイルが、現行APIで確認できない旧Hot Pepper行と旧ローカルデータを保持する入力になります。
3. リポジトリのルートで `make csv` を実行します。
   - 現行の Hot Pepper データは、まず `output/` 内の一時CSVへ取得されます。
   - 既存の公開CSVと重複統合してから、`docs/output/meatmap.csv` を置き換えます。
   - 公開サイト検査まで成功した場合だけ更新を確定し、失敗時は更新前の公開CSVへ戻します。
4. `make check` と `git diff --check` を実行します。
5. `make pages-serve` でローカル配信し、`http://localhost:8000/map_demo.html?csv=output/meatmap.csv` を確認します。
6. `git diff -- docs/output/meatmap.csv` などで、件数メタデータ、出典、確認状態が意図どおりか確認します。
7. `docs/output/meatmap.csv` を含む意図した変更をPRにします。PRを `main` にマージすると、通常は数分で公開サイトへ反映されます。

`python3 -m meatmap.cli --copy-to-docs` は公開更新に使いません。このオプションは Hot Pepper 単独CSVを直接配置するため、既存の旧ローカルデータが失われます。

## 公開物のルール

- `docs/output/` には `meatmap.csv` だけを置きます。一時ファイルは Git 管理対象外の `output/` に作成し、処理終了時に削除します。
- `sources`（データ系列）と `data_status`（確認状態）は独立して管理します。主な組み合わせは、現行Hot Pepper行が `hotpepper` / `current`、現行Hot Pepperと旧ローカルの統合行が `hotpepper,legacy_local` / `current`、現行APIで確認できない旧Hot Pepper行が `hotpepper` / `legacy_unverified`、旧ローカル単独行が `legacy_local` / `legacy_unverified` です。
- 旧データをこの更新フローで再取得・再検証したものとは扱いません。`legacy_local` だけの行には外部店舗ページのURLを公開しません。最新情報は店舗公式情報または店舗へ直接確認します。
- APIレスポンス、検索HTML、ローカル検証用URL・ID、座標キャッシュ、診断CSV、バックアップ、APIキー、PATを置きません。ローカル検証資料はGit管理対象外の `data/` 配下で扱います。
- 広告・アクセス解析を導入する場合は、先にプライバシーポリシーと同意要件を更新します。
- 店舗データを表示するページには、Hot Pepperデータの取得元であるホットペッパーグルメ Webサービスのクレジットを表示します。店舗ページリンクは `sources` に `hotpepper` を持ち、有効な Hot Pepper URLがある行にだけ表示します。
