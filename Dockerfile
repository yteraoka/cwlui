FROM python:3.8.5-slim-buster

WORKDIR /app
COPY cwlui.py requirements.txt /app/
COPY templates /app/templates/
COPY static /app/static/
RUN pip install -r requirements.txt

RUN groupadd -r app && useradd -r -g app app && chown -R app:app /app
USER app

EXPOSE 8000

CMD gunicorn -w 4 -t 65 -b 0.0.0.0:8000 --log-level=info --access-logfile=- --limit-request-line=8192 cwlui:app
