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

### TODO

- 表示 field の制御
- モダンな UI

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
