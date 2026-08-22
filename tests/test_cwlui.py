import re

import pytest

from conftest import ALLOWED_GROUP, DENIED_GROUP, EVENTS_PER_STREAM


def events_in(response):
    """Return the rendered log lines of a search result page."""
    body = response.get_data(as_text=True)
    return re.findall(r'<span class="event-set".*?</span></span>', body, re.S)


def next_token_in(response):
    match = re.search(r'name="next_token" value="([^"]*)"', response.get_data(as_text=True))
    return match.group(1) if match else None


def test_health_and_version(cwlui_app):
    cwlui = cwlui_app()
    client = cwlui.app.test_client()
    assert client.get('/health').get_data(as_text=True) == 'ok'
    assert client.get('/version').get_data(as_text=True) == cwlui.VERSION
    assert client.get('/').headers['Location'].endswith('/groups')


def test_groups_are_filtered_by_log_group_patterns(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='/aws/containerinsights/xxx/.*,/aws/lambda/xxx.*')
    body = cwlui.app.test_client().get('/groups').get_data(as_text=True)
    assert ALLOWED_GROUP in body
    assert DENIED_GROUP not in body


def test_streams_rejects_a_group_outside_the_patterns(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='/aws/containerinsights/xxx/.*')
    response = cwlui.app.test_client().get('/streams?group=' + DENIED_GROUP)
    assert response.status_code == 403


def test_search_rejects_a_group_outside_the_patterns(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='/aws/containerinsights/xxx/.*')
    response = cwlui.app.test_client().get('/search?group=' + DENIED_GROUP)
    assert response.status_code == 403


def test_an_empty_pattern_entry_does_not_disable_the_restriction(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='/aws/containerinsights/xxx/.*,')
    assert len(cwlui.LOG_GROUP_PATTERNS) == 1
    assert cwlui.is_accessible_group(ALLOWED_GROUP)
    assert not cwlui.is_accessible_group(DENIED_GROUP)


def test_no_patterns_allows_every_group(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    assert cwlui.LOG_GROUP_PATTERNS == []
    assert cwlui.is_accessible_group(DENIED_GROUP)
    assert not cwlui.is_accessible_group('')


def test_an_invalid_pattern_is_fatal_rather_than_fail_open(cwlui_app):
    with pytest.raises(ValueError):
        cwlui_app(LOG_GROUP_PATTERNS='/aws/[')


def test_search_returns_events(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    response = cwlui.app.test_client().get('/search?group=%s&stream=stream-0' % ALLOWED_GROUP)
    assert response.status_code == 200
    assert len(events_in(response)) == EVENTS_PER_STREAM + 1


def test_paging_does_not_drop_events(cwlui_app):
    """MAX_EVENTS truncating in the middle of a page must not skip the rest."""
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='', MAX_EVENTS='7', PAGE_SIZE='5')
    client = cwlui.app.test_client()

    seen = []
    url = '/search?group=%s&stream=stream-0' % ALLOWED_GROUP
    for _ in range(20):
        response = client.get(url)
        assert response.status_code == 200
        seen += events_in(response)
        token = next_token_in(response)
        if not token:
            break
        url = '/search?group=%s&stream=stream-0&next_token=%s' % (ALLOWED_GROUP, token)

    assert len(seen) == EVENTS_PER_STREAM + 1
    assert len(set(seen)) == len(seen)


def test_fields_stay_aligned_when_a_jsonpath_does_not_match(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    exps, error = cwlui.parse_fields(['$.nope', '$.kubernetes.pod_name', '$.data.status'])
    assert error is None

    message = '{"kubernetes": {"pod_name": "pod-0"}, "data": {"status": 200}}'
    assert cwlui.extract_fields(message, exps) == ['', 'pod-0', '200']


def test_non_json_lines_fall_back_to_the_raw_message(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    exps, _ = cwlui.parse_fields(['$.kubernetes.pod_name'])
    assert cwlui.extract_fields('plain text line', exps) == []


def test_fields_search_renders_the_configured_columns(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    response = cwlui.app.test_client().get(
        '/search?group=%s&stream=stream-0&fields=%s' % (ALLOWED_GROUP, '$.nope,$.kubernetes.pod_name'))
    assert response.status_code == 200
    # the unmatched path renders as a placeholder, the matched one as its value
    assert '<span>-</span> <span>pod-0</span>' in response.get_data(as_text=True)


def test_an_invalid_jsonpath_is_a_400_not_a_crash(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    response = cwlui.app.test_client().get('/search?group=%s&fields=%s' % (ALLOWED_GROUP, '$$$bad['))
    assert response.status_code == 400
    assert 'jsonpath' in response.get_data(as_text=True)


def test_a_missing_group_is_a_400_not_a_crash(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    client = cwlui.app.test_client()
    assert client.get('/search').status_code == 400
    assert client.get('/streams').status_code == 400


def test_an_unknown_group_is_a_404_not_a_500(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    client = cwlui.app.test_client()
    assert client.get('/search?group=/no/such/group').status_code == 404
    assert client.get('/streams?group=/no/such/group').status_code == 404


def test_an_unparsable_time_is_reported_instead_of_silently_ignored(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    response = cwlui.app.test_client().get('/search?group=%s&start_time=garbage' % ALLOWED_GROUP)
    assert response.status_code == 200
    assert '日時として解釈できなかった' in response.get_data(as_text=True)


def test_start_time_after_end_time_is_rejected(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    response = cwlui.app.test_client().get(
        '/search?group=%s&start_time=2020/07/28 12:00:00&end_time=2020/07/28 11:00:00' % ALLOWED_GROUP)
    assert response.status_code == 400


@pytest.mark.parametrize('text', [
    '2020/07/28 12:34:56',
    '2020/07/28 12:34',
    '2020-07-28 12:34:56',
    '2020-07-28T12:34',
])
def test_accepted_datetime_formats(cwlui_app, text):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    assert cwlui.datetime_to_timestamp(text) is not None


@pytest.mark.parametrize('text', ['', None, 'garbage', '2020/13/45 99:99'])
def test_rejected_datetime_values(cwlui_app, text):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    assert cwlui.datetime_to_timestamp(text) is None


def test_datetime_round_trip_uses_the_display_timezone(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    timestamp = cwlui.datetime_to_timestamp('2020/07/28 12:34:56')
    assert cwlui.timestamp_to_str(timestamp).startswith('2020-07-28T12:34:56')


def test_env_int_falls_back_on_bad_values(cwlui_app, monkeypatch):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    monkeypatch.setenv('SOME_VALUE', 'not-a-number')
    assert cwlui.env_int('SOME_VALUE', 42) == 42
    monkeypatch.setenv('SOME_VALUE', '0')
    assert cwlui.env_int('SOME_VALUE', 42) == 1
    monkeypatch.delenv('SOME_VALUE')
    assert cwlui.env_int('SOME_VALUE', 42) == 42


def test_search_stops_at_the_deadline_and_keeps_a_resume_token(cwlui_app):
    """A search that runs out of time returns partial results, not an error."""
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='', MAX_EVENTS='1000', PAGE_SIZE='5', SEARCH_TIMEOUT='1')
    exps, _ = cwlui.parse_fields([])
    with cwlui.app.app_context():
        events, token, timed_out = cwlui.filter_events(
            group=ALLOWED_GROUP, streams=['stream-0'], jsonpath_exps=exps,
            deadline=0)  # already expired -> stop after the first page
    assert timed_out is True
    assert len(events) == 5
    assert token is not None


def test_multiple_streams_can_be_searched_together(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    response = cwlui.app.test_client().get(
        '/search?group=%s&stream=stream-0&stream=stream-1' % ALLOWED_GROUP)
    assert response.status_code == 200
    assert len(events_in(response)) == 2 * (EVENTS_PER_STREAM + 1)


def test_streams_page_offers_stream_checkboxes(cwlui_app):
    cwlui = cwlui_app(LOG_GROUP_PATTERNS='')
    body = cwlui.app.test_client().get('/streams?group=' + ALLOWED_GROUP).get_data(as_text=True)
    assert 'name="stream" value="stream-0"' in body
    assert 'type="checkbox"' in body
