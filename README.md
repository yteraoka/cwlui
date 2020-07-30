# CloudWatch Logs Viewer

様々な理由により CloudWatch Logs の Web console にアクセス出来ない環境むけのサーバー

- Python 3.8
- Flask
- Gunicorn
- boto3

## 機能

- 環境変数でアクセス可能なロググループを制限
- logGroup 全体、もしくは logStream に対して filter pattern と期間を指定しての検索
- 表示されたログをクリックで JSON は modal で見やすく表示
- 一覧表示の項目を jsonpath 形式で指定可能

ログ取得の paging 処理

- 環境変数 `MAX_EVENTS` で1回のリクエストで取得する最大のログ行数を指定、これを超えてログが取得可能な場合は nextToken が返され、続きにアクセス可能。これを小さくしておくとレスポンスが速くなる。
- 環境変数 `PAGE_SIZE` で1ページあたりの最大行数を指定 (デフォルト 2000)

https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/logs.html#CloudWatchLogs.Paginator.FilterLogEvents

### TODO

- ~~表示 field の制御~~
- モダンな UI
- 複数の logStream を明示して検索

## Docker

```
docker run -d -v $HOME/.aws:/home/app/.aws:ro -e AWS_PROFILE=xxx \
  -e LOG_GROUP_PATTERNS='/aws/containerinsights/xxx/.*,/aws/lambda/xxx.*' \
  -p 8000:8000 yteraoka/cwlui:latest
```

## Dev

```
export FLASK_ENV=development
export FLASK_APP=cwlui.py
flask run
```

```
flask run --host=0.0.0.0
```
