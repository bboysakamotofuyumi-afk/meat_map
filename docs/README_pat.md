# GitHub Personal Access Token (PAT) の発行手順（HTTPS 用）

1. GitHub にログインし、右上のプロフィールアイコン → **Settings** を開く。
2. 左メニュー下部の **Developer settings** → **Personal access tokens** → **Tokens (classic)** を開く。
3. **Generate new token (classic)** をクリック。
4. Note に「meat_map deploy」など用途が分かる名前を記入し、Expiration（有効期限）を必要な期間に設定。
5. Scopes（権限）は最低限 **repo** にチェック（公開リポジトリのみなら public_repo でも可。private なら repo）。
6. **Generate token** を押すとトークン文字列が表示されるのでコピーする。この画面でしか表示されないため安全に保管すること。
7. EC2 上で一時的に環境変数にセット（例）:
   ```bash
   export GITHUB_TOKEN=<コピーしたトークン>
   git config credential.helper '!f() { echo username=token; echo password=$GITHUB_TOKEN; }; f'
   git remote set-url origin https://github.com/GenkiShimura2000/meat_map.git
   git push origin make-a-big-promise
   ```
8. プッシュ後、`git config --unset credential.helper` でヘルパー設定を戻すか、環境変数を `unset GITHUB_TOKEN` しておく。

セキュリティ注意:
- トークンはリポジトリやログに残さない（`.env` や `.gitignore` に含めない）。
- 使い終わったら GitHub 側で Revoke する、または短期期限のトークンを使う。***
