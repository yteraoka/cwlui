from flask import Flask, render_template, request, redirect, url_for
from datetime import timedelta, datetime, tzinfo
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
import botocore
from pytz import timezone, utc
from tzlocal import get_localzone
import time
from timeout_decorator import timeout, TimeoutError
from werkzeug.exceptions import HTTPException
import os
import re
import logging
import json
from jsonpath_ng import jsonpath, parse

VERSION = '0.2.1'

SEARCH_TIMEOUT = int(os.environ.get('SEARCH_TIMEOUT')) if os.environ.get('SEARCH_TIMEOUT') is not None else 60
LOG_STREAMS_MAX = int(os.environ.get('LOG_STREAMS_MAX')) if os.environ.get('LOG_STREAMS_MAX') is not None else 200
MAX_EVENTS = int(os.environ.get('MAX_EVENTS')) if os.environ.get('MAX_EVENTS') is not None else 4000
PAGE_SIZE = int(os.environ.get('PAGE_SIZE')) if os.environ.get('PAGE_SIZE') is not None else 1000

LOG_GROUP_PATTERNS = []
if os.environ.get('LOG_GROUP_PATTERNS') is not None:
    LOG_GROUP_PATTERNS = [re.compile(p) for p in os.environ.get('LOG_GROUP_PATTERNS').split(',')]

# use local timezone
TZ = get_localzone()

app = Flask(__name__.split('.')[0])

if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

# cloudwatch logs client
log_client = boto3.client('logs')


def is_accessible_group(group_name):
    if len(LOG_GROUP_PATTERNS) == 0:
        return True

    for pattern in LOG_GROUP_PATTERNS:
        if pattern.match(group_name) is not None:
            return True

    return False


def list_log_groups():
    log_groups = []
    paginator = log_client.get_paginator('describe_log_groups')
    for page in paginator.paginate():
        for group in page['logGroups']:
            if is_accessible_group(group['logGroupName']):
                log_groups.append(group['logGroupName'])

    return sorted(log_groups)


def list_log_streams(log_group_name):
    log_streams = []
    paginator = log_client.get_paginator('describe_log_streams')
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
    result = None
    try:
        (date_str, time_str) = timestr.split(' ')
        (year, month, day) = date_str.split('/')
        t = time_str.split(':')
        if len(t) == 2:
            t.append('0')
        result = datetime(int(year), int(month), int(day), hour=int(t[0]), minute=int(t[1]), second=int(t[2])).timestamp() * 1000
    except Exception as e:
        result = None

    return result


@timeout(SEARCH_TIMEOUT, use_signals=False)
def filter_events(group, streams=[], fields=[], stream_prefix=None, start_time=None, end_time=None, filter_pattern=None, token=None):
    request = {
            'logGroupName': group,
            'PaginationConfig': {
                'MaxItems': MAX_EVENTS,
                'PageSize': PAGE_SIZE
            }
    }

    if len(streams) > 0:
        request['logStreamNames'] = streams
    if stream_prefix:
        request['logStreamNamePrefix'] = stream_prefix
    if start_time:
        request['startTime'] = int(start_time)
    if end_time:
        request['endTime'] = int(end_time)
    if filter_pattern:
        request['filterPattern'] = filter_pattern
    if token:
        request['PaginationConfig']['StartingToken'] = token

    app.logger.info(request)

    jsonpath_exps = []
    for field in fields:
        jsonpath_exps.append(parse(field))

    events = []
    next_token = None

#    paginator = log_client.get_paginator('filter_log_events')
#    for page in paginator.paginate(**request):
#        if 'events' in page:
#            for event in page['events']:
#                log = {
#                    'message': event['message'],
#                    'timestamp': timestamp_to_str(event['timestamp']),
#                    'ingestion_time': timestamp_to_str(event['ingestionTime']),
#                    'stream': event['logStreamName'],
#                    'event_id': event['eventId'],
#                    'fields': []
#                }
#                try:
#                    j = json.loads(event['message'])
#                    for exp in jsonpath_exps:
#                        match = exp.find(j)
#                        if isinstance(match[0].value, str):
#                            log['fields'].append(match[0].value)
#                        else:
#                            log['fields'].append(json.dumps(match[0].value))
#                except Exception as e:
#                    app.logger.debug(e)
#                events.append(log)
#        if 'searchedLogStreams' in page:
#            for stream in page['searchedLogStreams']:
#                app.logger.debug("searched {}: {}".format(stream['logStreamName'], stream['searchedCompletely']))
#        next_token = page['nextToken'] if 'nextToken' in page else None

    while True:
        response = log_client.filter_log_events(**request)
        if 'events' in response:
            for event in response['events']:
                app.logger.info(event)
                log = {
                    'message': event['message'],
                    'timestamp': timestamp_to_str(event['timestamp']),
                    'ingestion_time': timestamp_to_str(event['ingestionTime']),
                    'stream': event['logStreamName'],
                    'event_id': event['eventId'],
                    'fields': []
                }
                try:
                    j = json.loads(event['message'])
                    for exp in jsonpath_exps:
                        match = exp.find(j)
                        if isinstance(match[0].value, str):
                            log['fields'].append(match[0].value)
                        else:
                            log['fields'].append(json.dumps(match[0].value))
                except Exception as e:
                    app.logger.debug(e)
                events.append(log)
        if 'nextToken' in response:
            request['nextToken'] = response['nextToken']
        else:
            break

    return events, next_token


@app.errorhandler(NoCredentialsError)
@app.errorhandler(ClientError)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    app.logger.warning(e)
    return render_template("error.html", e=e), 500


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
    data = {
        'log_group_name': log_group_name,
        'log_streams': list_log_streams(log_group_name),
        'max_streams': LOG_STREAMS_MAX
    }
    data['more_streams'] = True if (len(data['log_streams']) >= LOG_STREAMS_MAX) else False
    return render_template('streams.html', data=data)


@app.route('/search')
def search():
    log_group_name = request.args.get('group')
    streams = [s for s in request.args.getlist('stream') if s != '']
    fields = [s.strip() for s in request.args.get('fields').split(',') if s.strip() != ''] if request.args.get('fields') is not None else []
    start_time = datetime_to_timestamp(request.args.get('start_time'))
    end_time = datetime_to_timestamp(request.args.get('end_time'))
    next_token = request.args.get('next_token')
    filter_pattern = request.args.get('filter_pattern')
    message = None

    if not is_accessible_group(log_group_name):
        return render_template("error.html", e='許可されていない logGroup です'), 403

    t_start = time.time()

    try:
        events, next_token = filter_events(group=log_group_name, streams=streams,
                start_time=start_time, end_time=end_time,
                filter_pattern=filter_pattern, token=next_token,
                fields=fields)
    except TimeoutError:
        app.logger.error("TimeoutError")
        events = []
        message = '時間がかかりすぎています。対象期間を絞ってください。'

    duration = time.time() - t_start

    num_events = len(events)

    return render_template('search_result.html',
            data={'log_group_name': log_group_name, 'streams': streams, 'events': events, 'next_token': next_token,
                'num_events': num_events,
                'start_time': request.args.get('start_time') if (start_time) else '' ,
                'end_time': request.args.get('end_time') if (end_time) else '',
                'filter_pattern': filter_pattern if (filter_pattern) else '',
                'fields': ', '.join(fields),
                'duration': duration,
                'message': message
                })


@app.route('/health')
def healthcheck():
    return 'ok'


@app.route('/version')
def version():
    return VERSION

