# CloudWatch Logs Viewer

様々な理由により CloudWatch Logs の Web console にアクセス出来ない環境(あるいはセッション切れが早くて鬱陶しい場合)向けのサーバー

- Python 3.11 以降 (コンテナイメージは 3.14)
- Flask
- Gunicorn
- boto3

## Screenshot

### Search result

<img src="https://user-images.githubusercontent.com/52259/88941488-08209f00-d2c4-11ea-9393-992b12950ca2.png" width="480px">

一覧に表示する項目を指定することができます (Insights ではない通常の CloudWatch Logs UI ではできない)

### Search result show a log in modal

<img src="https://user-images.githubusercontent.com/52259/88941501-0b1b8f80-d2c4-11ea-8395-233ca2d9e86d.png" width="480px">

## 機能

- 環境変数でアクセス可能なロググループを制限
- logGroup 全体、もしくは logStream (複数選択可) に対して filter pattern と期間を指定しての検索
- 表示されたログをクリックで JSON は modal で見やすく表示
- 一覧表示の項目を jsonpath 形式で指定可能

## AWS の認証情報と権限

boto3 の標準の方法 (環境変数、`~/.aws`、EC2/ECS/EKS の IAM ロールなど) で認証情報を解決します。
リージョンは `AWS_DEFAULT_REGION` もしくは `AWS_REGION` で指定してください。

必要な IAM 権限は以下の 3 つです。

```
logs:DescribeLogGroups
logs:DescribeLogStreams
logs:FilterLogEvents
```

## 環境変数

| 変数名 | デフォルト | 説明 |
| --- | --- | --- |
| `LOG_GROUP_PATTERNS` | (制限なし) | アクセスを許可するロググループの正規表現をカンマ区切りで指定 |
| `MAX_EVENTS` | `4000` | 1リクエストで取得する最大のログ行数 |
| `PAGE_SIZE` | `1000` | CloudWatch Logs API 1回あたりの最大行数 |
| `SEARCH_TIMEOUT` | `60` | 検索を打ち切るまでの秒数 |
| `LOG_STREAMS_MAX` | `200` | logStream 一覧に表示する最大件数 (LastEventTimestamp の新しい順) |

### LOG_GROUP_PATTERNS

カンマ区切りで正規表現を並べます。1つも指定しない場合はすべてのロググループにアクセスできます。

```
LOG_GROUP_PATTERNS='/aws/containerinsights/xxx/.*,/aws/lambda/xxx.*'
```

- **正規表現は先頭からの部分一致 (`re.match`) です。末尾は固定されません。**
  `/aws/lambda/prod` は `/aws/lambda/production-secret` にもマッチしてしまうので、
  意図した範囲だけを許可したい場合は `/aws/lambda/prod$` や `/aws/lambda/prod-.*` のように書いてください。
- 空の要素は無視されます (末尾のカンマで制限が無効化されることはありません)。
- 正規表現として不正な値を指定した場合は、制限なしで起動してしまわないよう起動時にエラーになります。

この制限は `/groups` の一覧だけでなく `/streams` と `/search` でも適用され、
許可されていないロググループを直接指定した場合は 403 を返します。

### ログ取得の paging 処理

- `MAX_EVENTS` で1回のリクエストで取得する最大のログ行数を指定、これを超えてログが取得可能な場合は nextToken が返され、続きにアクセス可能。これを小さくしておくとレスポンスが速くなる。
- `PAGE_SIZE` で CloudWatch Logs API 1回あたりの最大行数を指定。

https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/logs.html#CloudWatchLogs.Paginator.FilterLogEvents

### 検索のタイムアウト

`SEARCH_TIMEOUT` 秒を超えた検索は打ち切られますが、**そこまでに取得できたログは表示され、
`Next` ボタンで続きを取得できます**。結果が 0 件になることはありません。

## TODO

- ~~表示 field の制御~~
- モダンな UI
- ~~複数の logStream を明示して検索~~

## Docker

```
docker run -d -v $HOME/.aws:/home/app/.aws:ro -e AWS_PROFILE=xxx \
  -e LOG_GROUP_PATTERNS='/aws/containerinsights/xxx/.*,/aws/lambda/xxx.*' \
  -p 8000:8000 yteraoka/cwlui:latest
```

## Dev

```
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

```
.venv/bin/flask --app cwlui run
```

```
.venv/bin/flask --app cwlui run --debug --host=0.0.0.0
```

`uv` を使う場合は以下でも起動できます。

```
uv run flask --app cwlui run
```

### Test

CloudWatch Logs は [moto](https://github.com/getmoto/moto) でモックしているので AWS の認証情報は不要です。

```
.venv/bin/pytest
```
