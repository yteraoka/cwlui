import importlib
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AWS_ENV = {
    'AWS_DEFAULT_REGION': 'ap-northeast-1',
    'AWS_ACCESS_KEY_ID': 'testing',
    'AWS_SECRET_ACCESS_KEY': 'testing',
    'AWS_SECURITY_TOKEN': 'testing',
    'AWS_SESSION_TOKEN': 'testing',
}

ALLOWED_GROUP = '/aws/containerinsights/xxx/application'
DENIED_GROUP = '/aws/lambda/secret-one'
GROUPS = [ALLOWED_GROUP, '/aws/lambda/xxxfunc', DENIED_GROUP]

EVENTS_PER_STREAM = 30
STREAM_COUNT = 3


@pytest.fixture
def cwlui_app(monkeypatch):
    """Import cwlui against a mocked CloudWatch Logs populated with known events.

    Yields a factory so a test can pick its own environment before import.
    """
    from moto import mock_aws

    started = []

    def _make(**env):
        for key, value in dict(AWS_ENV, **env).items():
            monkeypatch.setenv(key, value)

        mock = mock_aws()
        mock.start()
        started.append(mock)

        import boto3
        client = boto3.client('logs')
        for group in GROUPS:
            client.create_log_group(logGroupName=group)

        now = int(time.time() * 1000)
        for index in range(STREAM_COUNT):
            stream = 'stream-%d' % index
            client.create_log_stream(logGroupName=ALLOWED_GROUP, logStreamName=stream)
            events = [{
                'timestamp': now - (EVENTS_PER_STREAM - i) * 1000,
                'message': json.dumps({
                    'kubernetes': {'pod_name': 'pod-%d' % index, 'container_name': 'nginx'},
                    'data': {'uri': '/x/%d' % i, 'status': 200},
                }),
            } for i in range(EVENTS_PER_STREAM)]
            events.append({'timestamp': now, 'message': 'plain text line %d' % index})
            client.put_log_events(logGroupName=ALLOWED_GROUP, logStreamName=stream, logEvents=events)

        sys.modules.pop('cwlui', None)
        cwlui = importlib.import_module('cwlui')
        cwlui.app.config['TESTING'] = True
        return cwlui

    yield _make

    for mock in started:
        mock.stop()
    sys.modules.pop('cwlui', None)
