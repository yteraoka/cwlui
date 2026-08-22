from flask import Flask, render_template, request, redirect, url_for
from datetime import timedelta, datetime
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from tzlocal import get_localzone
import time
from werkzeug.exceptions import HTTPException
import os
import re
import logging
import json
from jsonpath_ng import parse

VERSION = '0.3.0'


def env_int(name, default, minimum=1):
    """Read a positive int from the environment, falling back to `default`."""
    value = os.environ.get(name)
    if value is None or value.strip() == '':
        return default
    try:
        number = int(value)
    except ValueError:
        logging.warning("%s='%s' is not an integer, using %d", name, value, default)
        return default
    if number < minimum:
        logging.warning("%s=%d is too small, using %d", name, number, minimum)
        return minimum
    return number


def compile_log_group_patterns(value):
    """Compile the comma separated regexp list given in LOG_GROUP_PATTERNS.

    Empty entries are dropped so that a stray comma cannot silently turn the
    restriction off, and an invalid regexp is fatal rather than fail-open.
    """
    patterns = []
    for pattern in (value or '').split(','):
        pattern = pattern.strip()
        if pattern == '':
            continue
        try:
            patterns.append(re.compile(pattern))
        except re.error as e:
            raise ValueError("LOG_GROUP_PATTERNS has an invalid regexp '{}': {}".format(pattern, e))
    return patterns


SEARCH_TIMEOUT = env_int('SEARCH_TIMEOUT', 60)
LOG_STREAMS_MAX = env_int('LOG_STREAMS_MAX', 200)
MAX_EVENTS = env_int('MAX_EVENTS', 4000)
PAGE_SIZE = env_int('PAGE_SIZE', 1000)

LOG_GROUP_PATTERNS = compile_log_group_patterns(os.environ.get('LOG_GROUP_PATTERNS'))

# accepted formats for the StartTime / EndTime form fields
DATETIME_FORMATS = (
    '%Y/%m/%d %H:%M:%S',
    '%Y/%m/%d %H:%M',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M',
    '%Y-%m-%dT%H:%M:%S',
    '%Y-%m-%dT%H:%M',
)

# use local timezone
TZ = get_localzone()

app = Flask(__name__.split('.')[0])

if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

# Bound every API call so that a hung connection cannot outlive SEARCH_TIMEOUT
# by much, and retry the throttling that CloudWatch Logs is fond of.
BOTO_CONFIG = Config(
    connect_timeout=10,
    read_timeout=max(10, min(SEARCH_TIMEOUT, 60)),
    retries={'max_attempts': 3, 'mode': 'standard'},
    user_agent_extra='cwlui/{}'.format(VERSION),
)

# cloudwatch logs client, created lazily so that a missing region or missing
# credentials shows up as an error page instead of an import time crash.
_log_client = None


def get_log_client():
    global _log_client
    if _log_client is None:
        _log_client = boto3.client('logs', config=BOTO_CONFIG)
    return _log_client


def is_accessible_group(group_name):
    if not group_name:
        return False

    if len(LOG_GROUP_PATTERNS) == 0:
        return True

    for pattern in LOG_GROUP_PATTERNS:
        if pattern.match(group_name) is not None:
            return True

    return False


def list_log_groups():
    log_groups = []
    paginator = get_log_client().get_paginator('describe_log_groups')
    for page in paginator.paginate():
        for group in page['logGroups']:
            if is_accessible_group(group['logGroupName']):
                log_groups.append(group['logGroupName'])

    return sorted(log_groups)


def list_log_streams(log_group_name):
    log_streams = []
    paginator = get_log_client().get_paginator('describe_log_streams')
    for page in paginator.paginate(logGroupName=log_group_name, orderBy='LastEventTime', descending=True):
        for stream in page['logStreams']:
            log_streams.append({
                'name': stream['logStreamName'],
                'last_event_timestamp': timestamp_to_str(stream['lastEventTimestamp']) if ('lastEventTimestamp' in stream) else ''
            })
            if len(log_streams) >= LOG_STREAMS_MAX:
                break
        if len(log_streams) >= LOG_STREAMS_MAX:
            break

    return log_streams


def timestamp_to_str(timestamp):
    dt = datetime.fromtimestamp(timestamp//1000, TZ)
    dt = dt + timedelta(microseconds=timestamp % 1000 * 1000)
    return dt.isoformat(timespec='milliseconds')


def datetime_to_timestamp(timestr):
    """Parse a form supplied local time into epoch milliseconds.

    Returns None when `timestr` is empty or cannot be parsed; the caller is
    expected to tell the user when a non empty value was rejected.
    """
    if timestr is None or timestr.strip() == '':
        return None

    for fmt in DATETIME_FORMATS:
        try:
            dt = datetime.strptime(timestr.strip(), fmt)
        except ValueError:
            continue
        # the form is documented as local time, so anchor it to the timezone
        # the timestamps are rendered in rather than to the process timezone
        return int(dt.replace(tzinfo=TZ).timestamp() * 1000)

    return None


def parse_fields(fields):
    """Compile the comma separated jsonpath list from the Fields form field.

    Returns (expressions, error_message).
    """
    expressions = []
    for field in fields:
        try:
            expressions.append(parse(field))
        except Exception as e:
            # jsonpath-ng raises JSONPathError for most bad input, but its ply
            # based lexer can leak other exception types too
            return None, "jsonpath '{}' を解釈できませんでした: {}".format(field, e)
    return expressions, None


def extract_fields(message, jsonpath_exps):
    """Pull the configured jsonpath values out of a single log message.

    An empty list means "this line has no fields to show", which the template
    renders as the raw message. A line that *is* JSON always yields one entry
    per configured field -- an unmatched path becomes an empty string -- so the
    columns stay aligned instead of collapsing back to the raw message.
    """
    if len(jsonpath_exps) == 0:
        return []

    try:
        parsed = json.loads(message)
    except ValueError:
        return []

    fields = []
    for exp in jsonpath_exps:
        matches = exp.find(parsed)
        if len(matches) == 0:
            fields.append('')
            continue
        value = matches[0].value
        fields.append(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))

    return fields


def filter_events(group, streams=None, jsonpath_exps=None, stream_prefix=None,
                  start_time=None, end_time=None, filter_pattern=None,
                  token=None, deadline=None):
    """Run FilterLogEvents and return (events, next_token, timed_out).

    `deadline` is a time.monotonic() value; when it passes we stop early and
    hand back the token for the next page instead of raising, so the user keeps
    whatever was already fetched.
    """
    streams = streams or []
    jsonpath_exps = jsonpath_exps or []

    params = {
        'logGroupName': group,
        'PaginationConfig': {
            'MaxItems': MAX_EVENTS,
            'PageSize': PAGE_SIZE
        }
    }

    if len(streams) > 0:
        params['logStreamNames'] = streams
    elif stream_prefix:
        # FilterLogEvents rejects logStreamNames and logStreamNamePrefix together
        params['logStreamNamePrefix'] = stream_prefix
    if start_time:
        params['startTime'] = int(start_time)
    if end_time:
        params['endTime'] = int(end_time)
    if filter_pattern:
        params['filterPattern'] = filter_pattern
    if token:
        params['PaginationConfig']['StartingToken'] = token

    app.logger.info(params)

    events = []
    timed_out = False
    last_page_token = None

    paginator = get_log_client().get_paginator('filter_log_events')
    page_iterator = paginator.paginate(**params)
    for page in page_iterator:
        for event in page.get('events', []):
            events.append({
                'message': event['message'],
                'timestamp': timestamp_to_str(event['timestamp']),
                'ingestion_time': timestamp_to_str(event['ingestionTime']),
                'stream': event['logStreamName'],
                'event_id': event['eventId'],
                'fields': extract_fields(event['message'], jsonpath_exps)
            })
        for stream in page.get('searchedLogStreams', []):
            app.logger.debug("searched {}: {}".format(stream['logStreamName'], stream['searchedCompletely']))

        last_page_token = page.get('nextToken')
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            break

    if timed_out:
        # the whole page was consumed, so the service token resumes exactly
        # after the events we are about to render
        next_token = last_page_token
    else:
        # resume_token accounts for MAX_EVENTS truncating in the middle of a
        # page; the raw nextToken of the last page would skip the remainder
        next_token = page_iterator.resume_token

    return events, next_token, timed_out


@app.errorhandler(BotoCoreError)
@app.errorhandler(ClientError)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    app.logger.warning(e)
    status = 500
    if isinstance(e, ClientError):
        code = e.response.get('Error', {}).get('Code')
        if code == 'ResourceNotFoundException':
            status = 404
        elif code in ('AccessDeniedException', 'AccessDenied', 'UnauthorizedOperation'):
            status = 403
    return render_template("error.html", e=e), status


@app.route('/')
def index():
    return redirect(url_for('groups'))


@app.route('/groups')
def groups():
    data = {
        'log_groups': list_log_groups()
    }
    return render_template('groups.html', data=data)


@app.route('/streams')
def streams():
    log_group_name = request.args.get('group')

    if not log_group_name:
        return render_template("error.html", e='logGroup が指定されていません'), 400

    if not is_accessible_group(log_group_name):
        return render_template("error.html", e='許可されていない logGroup です'), 403

    data = {
        'log_group_name': log_group_name,
        'log_streams': list_log_streams(log_group_name),
        'streams': [s for s in request.args.getlist('stream') if s != ''],
        'max_streams': LOG_STREAMS_MAX
    }
    data['more_streams'] = True if (len(data['log_streams']) >= LOG_STREAMS_MAX) else False
    return render_template('streams.html', data=data)


@app.route('/search')
def search():
    log_group_name = request.args.get('group')

    if not log_group_name:
        return render_template("error.html", e='logGroup が指定されていません'), 400

    if not is_accessible_group(log_group_name):
        return render_template("error.html", e='許可されていない logGroup です'), 403

    streams = [s for s in request.args.getlist('stream') if s != '']
    fields = [s.strip() for s in request.args.get('fields', '').split(',') if s.strip() != '']
    next_token = request.args.get('next_token')
    filter_pattern = request.args.get('filter_pattern')
    messages = []

    jsonpath_exps, field_error = parse_fields(fields)
    if field_error is not None:
        return render_template("error.html", e=field_error), 400

    start_time = datetime_to_timestamp(request.args.get('start_time'))
    end_time = datetime_to_timestamp(request.args.get('end_time'))
    for label, raw, parsed in (('StartTime', request.args.get('start_time'), start_time),
                               ('EndTime', request.args.get('end_time'), end_time)):
        if raw is not None and raw.strip() != '' and parsed is None:
            messages.append('{} 「{}」を日時として解釈できなかったため無視しました (例: 2020/07/28 12:34:56)。'.format(label, raw))

    if start_time and end_time and start_time > end_time:
        return render_template("error.html", e='StartTime が EndTime よりも後になっています'), 400

    t_start = time.time()

    events, next_token, timed_out = filter_events(
        group=log_group_name, streams=streams,
        start_time=start_time, end_time=end_time,
        filter_pattern=filter_pattern, token=next_token,
        jsonpath_exps=jsonpath_exps,
        deadline=time.monotonic() + SEARCH_TIMEOUT)

    duration = time.time() - t_start

    if timed_out:
        app.logger.warning("search timed out after %.1fs (%d events)", duration, len(events))
        messages.append('{} 秒を超えたため検索を打ち切りました。取得できた分のみ表示しています'
                        '(続きは Next で取得できます)。対象期間を絞ると速くなります。'.format(SEARCH_TIMEOUT))

    return render_template('search_result.html',
            data={'log_group_name': log_group_name, 'streams': streams, 'events': events, 'next_token': next_token,
                'num_events': len(events),
                'start_time': request.args.get('start_time') if (start_time) else '',
                'end_time': request.args.get('end_time') if (end_time) else '',
                'filter_pattern': filter_pattern if (filter_pattern) else '',
                'fields': ', '.join(fields),
                'duration': duration,
                'timed_out': timed_out,
                'messages': messages
                })


@app.route('/health')
def healthcheck():
    return 'ok'


@app.route('/version')
def version():
    return VERSION
