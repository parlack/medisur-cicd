#!/usr/bin/env python3
"""Read-only VM-TOOLS evidence collector; run as root after deployment finishes.

Requires Python 3.10+ and existing server secrets; installs/configures nothing.
Only GETs fixed loopback APIs. Cluster evidence comes from Prometheus, not
kubectl or Jenkins credential APIs. Output is an explicit server-local path.
Empty query results remain empty (no synthetic zero traffic or coverage).
"""

import argparse
import base64
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request


SECRET_DIR = Path('/etc/medisur/secrets')
SERVICES = {
    'jenkins': ('http://127.0.0.1:8080', 'medisur-admin', 'jenkins_api_token'),
    'sonar': ('http://127.0.0.1:9000', 'admin', 'sonar_admin_password'),
    'prometheus': ('http://127.0.0.1:9090', None, None),
    'grafana': ('http://127.0.0.1:3000', 'medisur-admin', 'grafana_admin_password'),
}
EXPECTED_JOBS = {'prometheus', 'node', 'medisur', 'kubernetes-cadvisor', 'kube-state-metrics'}
METRICS = ('coverage', 'bugs', 'vulnerabilities', 'code_smells')
QUERIES = {
    'up': 'up',
    'app_rps': 'sum by (pod, version) (rate(http_requests_total{job="medisur",route!~"/metrics|/health|/ready"}[5m]))',
    'cadvisor_cpu_cores': 'sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{job="kubernetes-cadvisor",namespace="medisur",container!="",container!="POD",pod!=""}[5m]))',
    'cadvisor_memory_bytes': 'sum by (namespace, pod) (container_memory_working_set_bytes{job="kubernetes-cadvisor",namespace="medisur",container!="",container!="POD",pod!=""})',
    'ksm_ready_replicas': 'kube_deployment_status_replicas_ready{namespace="medisur",job="kube-state-metrics"}',
    'ksm_desired_replicas': 'kube_deployment_spec_replicas{namespace="medisur",job="kube-state-metrics"}',
}


class CollectionError(Exception):
    """Messages are fixed codes, never server bodies or exception details."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise CollectionError('REDIRECT_BLOCKED')


def fields(value, names):
    if not isinstance(value, dict):
        raise CollectionError('INVALID_RESPONSE')
    result = {}
    for name in names:
        item = value[name]
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise CollectionError('INVALID_RESPONSE')
        if isinstance(item, float) and not math.isfinite(item):
            raise CollectionError('INVALID_RESPONSE')
        result[name] = item
    return result


def rows(value):
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise CollectionError('INVALID_RESPONSE')
    return value


def count(value):
    if type(value) is not int or value < 0:
        raise CollectionError('INVALID_RESPONSE')
    return value


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect())
        self.headers = {}
        self.redactions = set()

    def get(self, service, path, params=None):
        base, username, filename = SERVICES[service]
        if not path.startswith('/') or path.startswith('//') or '?' in path:
            raise CollectionError('INVALID_ENDPOINT')
        if service not in self.headers:
            headers = {'Accept': 'application/json'}
            if filename:
                try:
                    password = (SECRET_DIR / filename).read_text().strip()
                except (OSError, UnicodeError):
                    raise CollectionError('SECRET_UNAVAILABLE') from None
                if not password:
                    raise CollectionError('SECRET_UNAVAILABLE')
                encoded = base64.b64encode(f'{username}:{password}'.encode()).decode()
                self.redactions.update((password, encoded, urllib.parse.quote(password, safe='')))
                headers['Authorization'] = 'Basic ' + encoded
            self.headers[service] = headers
        url = base + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers=self.headers[service], method='GET')
        with self.opener.open(request, timeout=20) as response:
            if response.status != 200:
                raise CollectionError('HTTP_' + str(response.status))
            body = response.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise CollectionError('RESPONSE_TOO_LARGE')
        return json.loads(body)

    def sanitize(self, value):
        if isinstance(value, str):
            for secret in sorted(self.redactions, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
            return value
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, dict):
            return {self.sanitize(key): self.sanitize(item) for key, item in value.items()}
        return value


class Collector:
    def __init__(self, client):
        self.client = client
        self.errors = []

    def attempt(self, section, operation):
        try:
            return operation()
        except urllib.error.HTTPError as error:
            code = 'HTTP_' + str(error.code)
            error.close()
        except CollectionError as error:
            code = str(error)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            code = 'CONNECTION_FAILED'
        except Exception:
            # JSON/schema failures must not expose raw payloads in a traceback.
            code = 'INVALID_RESPONSE'
        self.errors.append({'section': section, 'code': code})
        return None

    def build(self, path):
        data = self.client.get('jenkins', path + '/api/json', {
            'tree': 'number,result,building,timestamp,duration'})
        result = fields(data, ('number', 'result', 'building', 'timestamp', 'duration'))
        count(result['number'])
        if type(result['building']) is not bool:
            raise CollectionError('INVALID_RESPONSE')
        return result

    def workflow(self, path):
        data = self.client.get('jenkins', path + '/wfapi/describe')
        stages = [fields(stage, ('id', 'name', 'status', 'startTimeMillis', 'durationMillis'))
                  for stage in rows(data['stages'])]
        return {**fields(data, ('status',)), 'stages': stages, 'stage_count': len(stages)}

    def tests(self, path):
        data = self.client.get('jenkins', path + '/testReport/api/json', {
            'tree': 'failCount,skipCount,passCount,totalCount'})
        result = {name: count(data[name]) for name in ('failCount', 'skipCount')}
        result['totalCount'] = count(data['totalCount']) if 'totalCount' in data else sum(result.values()) + count(data['passCount'])
        if result['failCount'] + result['skipCount'] > result['totalCount']:
            raise CollectionError('INVALID_RESPONSE')
        return result

    def gate(self, project):
        data = self.client.get('sonar', '/api/qualitygates/project_status', {
            'projectKey': project})['projectStatus']
        result = fields(data, ('status',))
        result['conditions'] = [fields(condition, ('status', 'metricKey', 'comparator',
                                                  'errorThreshold', 'actualValue'))
                                for condition in rows(data['conditions'])]
        return result

    def measures(self, project):
        data = self.client.get('sonar', '/api/measures/component', {
            'component': project, 'metricKeys': ','.join(METRICS)})
        measures = {item['metric']: item['value'] for item in rows(data['component']['measures'])
                    if item['metric'] in METRICS and 'value' in item}
        for value in measures.values():
            if not isinstance(value, str) or not math.isfinite(float(value)):
                raise CollectionError('INVALID_RESPONSE')
        return measures

    def issues(self, project):
        data = self.client.get('sonar', '/api/issues/search', {
            'componentKeys': project, 'resolved': 'false', 'ps': 1})
        # Deliberately omit issues, messages, paths, authors and snippets.
        return {'unresolved_total': count(data['paging']['total'])}

    def targets(self):
        data = self.client.get('prometheus', '/api/v1/targets', {'state': 'active'})
        if data['status'] != 'success':
            raise CollectionError('API_FAILED')
        result = []
        for target in rows(data['data']['activeTargets']):
            labels = fields(target['labels'], ('job', 'instance'))
            result.append({**labels, **fields(target, ('health', 'lastScrape'))})
        return {'active_count': len(result),
                'healthy_count': sum(target['health'] == 'up' for target in result),
                'targets': result}

    def query(self, expression, timestamp):
        data = self.client.get('prometheus', '/api/v1/query', {
            'query': expression, 'time': timestamp})
        if data['status'] != 'success':
            raise CollectionError('API_FAILED')
        if data.get('warnings') or data.get('infos'):
            raise CollectionError('QUERY_ANNOTATIONS')
        if data['data']['resultType'] != 'vector':
            raise CollectionError('INVALID_RESPONSE')
        result = []
        for series in rows(data['data']['result']):
            labels = {key: value for key, value in series['metric'].items()
                      if key in ('job', 'instance', 'namespace', 'pod', 'version', 'deployment')
                      and isinstance(value, str)}
            timestamp_value, sample = series['value']
            if not isinstance(timestamp_value, (int, float)) or not isinstance(sample, str):
                raise CollectionError('INVALID_RESPONSE')
            if not math.isfinite(timestamp_value):
                raise CollectionError('INVALID_RESPONSE')
            float(sample)  # Keep Prometheus NaN/Inf strings as observed, not JSON NaN.
            result.append({'labels': labels, 'timestamp': timestamp_value, 'value': sample})
        return {'expression': expression, 'series': result}

    def dashboards(self):
        dashboards = {}
        for page in range(1, 101):
            data = rows(self.client.get('grafana', '/api/search', {
                'type': 'dash-db', 'limit': 100, 'page': page}))
            for dashboard in data:
                item = fields(dashboard, ('uid', 'title', 'type'))
                if item['type'] == 'dash-db':
                    dashboards[item['uid']] = item
            if len(data) < 100:
                return {'count': len(dashboards), 'dashboards': list(dashboards.values())}
        raise CollectionError('PAGINATION_LIMIT')

    def collect(self, job, project):
        started = datetime.now(timezone.utc).isoformat()
        job_path = ''.join('/job/' + urllib.parse.quote(part, safe='') for part in job.split('/'))
        build = self.attempt('jenkins.build', lambda: self.build(job_path + '/lastBuild'))
        result = {'schema_version': 1, 'started_at': started,
                  'scope': 'Loopback APIs; current Sonar project and Prometheus snapshot, not proven build-correlated.',
                  'jenkins': {'job': job, 'build': build}, 'sonar': {'project': project}}
        if build and build['building'] is False:
            # Pin related reports to the observed build, never a moving lastBuild alias.
            path = job_path + '/' + str(build['number'])
            result['jenkins']['workflow'] = self.attempt('jenkins.workflow', lambda: self.workflow(path))
            result['jenkins']['tests'] = self.attempt('jenkins.tests', lambda: self.tests(path))
            result['sonar']['quality_gate'] = self.attempt('sonar.quality_gate', lambda: self.gate(project))
            result['sonar']['metrics'] = self.attempt('sonar.metrics', lambda: self.measures(project))
            result['sonar']['issues'] = self.attempt('sonar.issues', lambda: self.issues(project))
            prometheus = {'targets': self.attempt('prometheus.targets', self.targets), 'queries': {}}
            timestamp = datetime.now(timezone.utc).isoformat()
            for name, expression in QUERIES.items():
                prometheus['queries'][name] = self.attempt(
                    'prometheus.' + name, lambda expr=expression: self.query(expr, timestamp))
            result['prometheus'] = prometheus
            result['grafana'] = self.attempt('grafana.dashboards', self.dashboards)
            latest = self.attempt('jenkins.final_build', lambda: self.build(job_path + '/lastBuild'))
            if latest != build:
                self.errors.append({'section': 'jenkins', 'code': 'BUILD_CHANGED_DURING_COLLECTION'})
        else:
            self.errors.append({'section': 'jenkins', 'code': 'DEPLOYMENT_NOT_COMPLETE'})
        result['errors'] = self.errors
        result['failures'] = success_failures(result)
        result['success'] = not result['failures']
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        return self.client.sanitize(result)


def success_failures(report):
    failures = []
    if report.get('errors'):
        failures.append('COLLECTION_INCOMPLETE')
    jenkins = report.get('jenkins') or {}
    build = jenkins.get('build') or {}
    if build.get('building') is not False or build.get('result') != 'SUCCESS':
        failures.append('JENKINS_NOT_GREEN')
    workflow = jenkins.get('workflow') or {}
    stages = workflow.get('stages') or []
    if (workflow.get('status') != 'SUCCESS' or not stages
            or not any(stage.get('name') == 'Deploy main and smoke test'
                       and stage.get('status') == 'SUCCESS' for stage in stages)):
        failures.append('DEPLOYMENT_NOT_VERIFIED')
    tests = jenkins.get('tests') or {}
    if tests.get('failCount') != 0 or not tests.get('totalCount'):
        failures.append('TESTS_NOT_GREEN')
    sonar = report.get('sonar') or {}
    if (sonar.get('quality_gate') or {}).get('status') != 'OK':
        failures.append('SONAR_GATE_NOT_OK')
    if set(METRICS) - (sonar.get('metrics') or {}).keys():
        failures.append('SONAR_METRICS_MISSING')
    prometheus = report.get('prometheus') or {}
    targets = (prometheus.get('targets') or {}).get('targets') or []
    if (not targets or any(target.get('health') != 'up' for target in targets)
            or EXPECTED_JOBS - {target.get('job') for target in targets}):
        failures.append('PROMETHEUS_TARGETS_NOT_UP')
    queries = prometheus.get('queries') or {}
    up = (queries.get('up') or {}).get('series') or []
    observed = {(item['labels'].get('job'), item['labels'].get('instance')): item['value']
                for item in up}
    if (not observed or any(float(value) != 1 for value in observed.values())
            or any(float(observed.get((target['job'], target['instance']), 'nan')) != 1
                   for target in targets)):
        failures.append('PROMETHEUS_UP_NOT_ONE')
    for name in QUERIES:
        query = queries.get(name)
        # No traffic may legitimately leave app_rps empty; never fabricate 0.
        if query is None or (name != 'app_rps' and not query.get('series')):
            failures.append('PROMETHEUS_QUERY_MISSING_' + name.upper())
        elif any(not math.isfinite(float(item['value'])) for item in query['series']):
            failures.append('PROMETHEUS_QUERY_NONFINITE_' + name.upper())
    if (report.get('grafana') or {}).get('count', 0) < 2:
        failures.append('GRAFANA_DASHBOARDS_LT_TWO')
    return failures


def write_report(output, report):
    output = Path(output)
    if (not output.is_absolute() or output.suffix != '.json' or output.is_symlink()
            or output.resolve().is_relative_to(SECRET_DIR.resolve())):
        raise CollectionError('INVALID_OUTPUT_PATH')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         prefix='.live-validation-', delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(report, stream, indent=2, ensure_ascii=True, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='Absolute server JSON artifact path')
    parser.add_argument('--require-success', action='store_true', help='Exit 1 for failed/incomplete validation')
    parser.add_argument('--job', default='medisur-main')
    parser.add_argument('--project', default='medisur-cicd')
    args = parser.parse_args(argv)
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        print('ROOT_ON_TOOLS_VM_REQUIRED', file=sys.stderr)
        return 2
    if not args.project or any(part in ('', '.', '..') for part in args.job.split('/')):
        print('INVALID_IDENTIFIER', file=sys.stderr)
        return 2
    try:
        report = Collector(Client()).collect(args.job, args.project)
        write_report(args.output, report)
    except Exception:
        print('COLLECTION_OR_ARTIFACT_FAILED', file=sys.stderr)
        return 2
    print('VALIDATION_SUCCESS' if report['success'] else 'VALIDATION_FAILED')
    return 1 if args.require_success and not report['success'] else 0


if __name__ == '__main__':
    sys.exit(main())