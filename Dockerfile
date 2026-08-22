FROM python:3.14.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY cwlui.py /app/
COPY templates /app/templates/

RUN groupadd -g 1000 app && useradd -u 1000 -g app -M -s /usr/sbin/nologin app && chown -R app:app /app
USER 1000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read() == b'ok' else 1)"]

CMD ["gunicorn", "-w", "4", "-t", "65", "-b", "0.0.0.0:8000", "--log-level=info", "--access-logfile=-", "--limit-request-line=8192", "cwlui:app"]
