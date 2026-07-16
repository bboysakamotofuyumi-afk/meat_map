# TOKYO MEAT MAP

Hot Pepper API の現行データと、既存の公開スナップショットを統合して表示する GitHub Pages の店舗マップです。現行データの自動取得元は Hot Pepper API です。現行APIで確認できない過去の店舗行は `legacy_unverified` として引き継ぎ、データ系列を `hotpepper` / `legacy_local` で区別します。旧ローカルデータの一次取得元は、現リポジトリの履歴からは特定できません。

## ローカル実行と公開までの最短手順

1. `HOTPEPPER_API_KEY` を環境変数、またはリポジトリ直下の `.env` に設定します。`.env` は Git 管理対象外です。
2. `make csv` を実行します。
   - Hot Pepper の S〜C ランクを `output/` 内の一時CSVへ取得します。
   - 既存の `docs/output/meatmap.csv` を旧データ入力にして重複統合します。
   - 統合後のCSVだけを `docs/output/meatmap.csv` へ反映し、公開サイト検査を実行します。
   - 成功・失敗を問わず、`output/` 内の一時CSVは削除します。取得・統合・検査に失敗した場合、公開CSVは更新前の内容へ戻します。
3. `make check` でテストと公開サイト検査を実行します。
4. `make pages-serve` を実行し、`http://localhost:8000/map_demo.html?csv=output/meatmap.csv` を確認します。
5. `docs/output/meatmap.csv` を含む意図した差分だけをPRにします。`main` への反映後、GitHub Pages が `docs/` を公開します。

公開URL: `https://bboysakamotofuyumi-afk.github.io/meat_map/`

## データ更新時の確認

更新前後に次を確認します。

```bash
make csv
make check
git diff --stat -- docs/output/meatmap.csv
git diff --check
```

`make csv` は、既存の統合済み公開CSVを旧データ入力として必要とします。ファイルがない場合は、空のデータセットを公開しないため処理を中止します。また、`python3 -m meatmap.cli --copy-to-docs` を直接実行すると Hot Pepper 単独CSVで公開ファイルを上書きするため、通常の公開更新には使いません。

## 公開データの扱い

- `docs/output/` に置くファイルは `meatmap.csv` だけです。
- 更新時にAPIから取得できた Hot Pepper 行は現行データです。現行APIで確認できない旧Hot Pepper行と、既存の旧ローカルデータは未検証のまま引き継ぎます。
- `sources`（`hotpepper` / `legacy_local`）、`legacy_id`、`data_status`、`last_verified_at` を使って現行データと旧ローカルデータを区別します。
- `legacy_local` だけの行には外部店舗ページのURLを公開しません。
- 店舗の営業状況、価格、住所などは変更される可能性があります。利用時は各店舗の公式情報も確認してください。
- APIレスポンス、検索HTML、ローカル検証用URL・ID、座標キャッシュ、診断CSV、バックアップ、APIキー、PATは公開しません。ローカル検証資料はGit管理対象外の `data/` 配下で扱います。

## APIキー

APIキーをコード、CSV、ログ、コミットへ含めないでください。たとえば `.env` を使う場合は次の形式です（実際の値は記載しません）。

```dotenv
HOTPEPPER_API_KEY=your_key_here
```

エラーを共有するときも、キーが含まれていないことを確認してください。
