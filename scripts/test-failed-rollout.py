"""Isolated live negative rollout driver. Run on VM-TOOLS; never logs secrets.

Upload this file and ci/rollback-demo.Jenkinsfile without committing or pushing.
--inspect is read-only. --execute creates a manual-only job, never updates main.
--collect writes actual archived observations to the requested evidence file.
"""
import argparse
import base64
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

JOB = "medisur-rollout-demo"
MAIN = "medisur-main"
SHA = "f04534bb54d0a16472ea97009fcebc409e43fb8c"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Jenkins:
    def __init__(self):
        token = Path('/etc/medisur/secrets/jenkins_api_token').read_text().strip()
        self.auth = 'Basic ' + base64.b64encode(('medisur-admin:' + token).encode()).decode()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, route, data=None, content_type='application/xml'):
        request = urllib.request.Request('http://127.0.0.1:8080' + route, data=data,
                                         headers={'Authorization': self.auth,
                                                  'Content-Type': content_type})
        with self.opener.open(request, timeout=30) as response:
            return response.read()

    def json(self, route):
        return json.loads(self.call(route))

    def idle(self):
        state = self.json('/job/' + MAIN + '/lastBuild/api/json?tree=number,result,building')
        if any(state.get(key) != value for key, value in
               {'number': 3, 'result': 'SUCCESS', 'building': False}.items()):
            raise RuntimeError('Main must remain idle at successful build 3')
        queue = self.json('/queue/api/json?tree=items[task[name]]')
        if queue['items']:
            raise RuntimeError('Jenkins queue must be empty')
        computers = self.json('/computer/api/json?tree=computer[executors[currentExecutable[url]],oneOffExecutors[currentExecutable[url]]]')
        if any(executor.get('currentExecutable') for computer in computers['computer']
               for executor in computer.get('executors', []) + computer.get('oneOffExecutors', [])):
            raise RuntimeError('Jenkins has an active executor')
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--inspect', action='store_true')
    action.add_argument('--execute', action='store_true')
    action.add_argument('--collect', type=int, metavar='BUILD')
    parser.add_argument('--pipeline', type=Path)
    parser.add_argument('--source-checksums', help='Base64 sha256sum manifest of local real deployment inputs')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    client = Jenkins()
    config = client.call('/job/' + MAIN + '/config.xml')
    config_hash = hashlib.sha256(config).hexdigest()
    scm = ET.fromstring(config).find('definition/scm')
    if scm is None:
        raise RuntimeError('Expected main SCM definition')
    remote = scm.find('userRemoteConfigs/hudson.plugins.git.UserRemoteConfig')
    url = remote.findtext('url')
    credential = remote.findtext('credentialsId')
    if url != 'https://github.com/SergioMultimedios/medisur-cicd.git':
        raise RuntimeError('Unexpected trusted repository URL: ' + str(url))
    if args.inspect:
        print(json.dumps({'main': client.idle(), 'main_config_sha256': config_hash,
                          'scm_url': url, 'scm_credential_id': credential,
                          'branches': [item.text for item in scm.findall('branches/*/name')]}))
        return
    if args.execute:
        if not args.pipeline or not args.source_checksums:
            parser.error('--execute requires --pipeline and --source-checksums')
        client.idle()
        try:
            client.call('/job/' + JOB + '/api/json')
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        else:
            raise RuntimeError('Demo job already exists; refusing overwrite or repeat mutation')
        checksums = base64.b64decode(args.source_checksums, validate=True).decode('ascii')
        script = args.pipeline.read_text(encoding='utf-8')
        for key, value in {'SCM_URL_JSON': url, 'SCM_CREDENTIAL_JSON': credential,
                           'SOURCE_CHECKSUMS_JSON': checksums, 'MAIN_CONFIG_HASH_JSON': config_hash}.items():
            script = script.replace('__' + key + '__', json.dumps(value))
        root = ET.Element('flow-definition', {'plugin': 'workflow-job'})
        ET.SubElement(root, 'description').text = ('Manual negative test; unchanged real deploy.sh; '
            'missing image; automatic rollback required. No publish, app writes, or main configuration changes.')
        ET.SubElement(root, 'keepDependencies').text = 'false'
        ET.SubElement(root, 'properties')
        definition = ET.SubElement(root, 'definition', {'class': 'org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition', 'plugin': 'workflow-cps'})
        ET.SubElement(definition, 'script').text = script
        ET.SubElement(definition, 'sandbox').text = 'true'
        ET.SubElement(root, 'triggers')
        ET.SubElement(root, 'disabled').text = 'false'
        client.call('/createItem?name=' + JOB, ET.tostring(root))
        client.idle()
        client.call('/job/' + JOB + '/build', b'')
        print(json.dumps({'job': JOB, 'queued': True, 'main_config_sha256': config_hash}))
        return
    if not args.output or args.output.exists():
        parser.error('--collect requires a new --output path')
    route = '/job/' + JOB + '/' + str(args.collect)
    state = client.json(route + '/api/json?tree=number,result,building,url,timestamp,duration')
    if state['building']:
        raise RuntimeError('Build still running; do not interrupt rollback')
    evidence = {'collected_utc': datetime.now(timezone.utc).isoformat(), 'build': state,
                'main_after': client.idle(), 'main_config_sha256_after': config_hash,
                'limitations': ['HTTP and pod observations are sampled, not proof of uninterrupted availability.',
                                'No SQLite writes or database rollback; single-node pilot only.',
                                'Isolated job SUCCESS means negative test passed; deploy.sh itself must exit nonzero.']}
    for name in ('before', 'after', 'before-pods', 'after-pods', 'result'):
        evidence[name] = client.json(route + '/artifact/evidence/' + name + '.json')
    evidence['samples'] = [json.loads(line) for line in client.call(route + '/artifact/evidence/samples.jsonl').decode().splitlines()]
    evidence['deploy_log'] = client.call(route + '/artifact/evidence/deploy.log').decode()
    evidence['source_checksums'] = client.call(route + '/artifact/evidence/source-checksums.txt').decode()
    before_hash = client.call(route + '/artifact/evidence/main-config-hash.txt').decode().strip()
    evidence['main_config_sha256_before'] = before_hash
    evidence['main_config_unchanged'] = before_hash == config_hash
    evidence['passed'] = (state['result'] == 'SUCCESS' and evidence['result']['automatic_rollback_verified']
                          and evidence['main_config_unchanged'])
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(evidence, output, indent=2)
        output.write('\n')
    print(json.dumps({'passed': evidence['passed'], 'build': state, 'result': evidence['result']}))
    if not evidence['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()