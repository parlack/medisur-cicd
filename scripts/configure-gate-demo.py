"""Configure, NEVER execute, the manual negative-control job on VM-TOOLS as root.

Run configure-services.py first. Reuse its loopback API helpers and protected
secret directory, but never call its main-project configuration functions.
The job checks out the main job's SCM using ci/gate-demo.Jenkinsfile. Commit
these files to trusted main before an operator explicitly starts the demo.
No push hook, polling schedule, build request, runtime publish, or deployment
is configured here. A red build proves blocking only when real tests pass,
Sonar reports coverage below 60%, and Would publish is skipped by the gate.

Isolation is operational, not a security boundary: both jobs use the trusted
lab controller with its host-root-equivalent Docker socket and global Jenkins
credential store. Do not allow untrusted repository writers or fork PRs.
"""

import argparse
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

MAIN_JOB = 'medisur-main'
DEMO_JOB = 'medisur-quality-demo'
DEMO_PROJECT = 'medisur-quality-demo'
DEMO_SCRIPT = 'ci/gate-demo.Jenkinsfile'
DEMO_TOKEN = 'sonar-demo-token'
GATE_NAME = 'MediSur 60 percent'
WEBHOOK_SECRET = 'sonarqube-webhook-secret'


def load_services():
    path = Path(__file__).with_name('configure-services.py')
    spec = importlib.util.spec_from_file_location('medisur_services', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load the local service configuration helpers')
    services = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(services)
    return services


def demo_pipeline_xml(main_xml):
    """Copy the actual main SCM, never main's triggers or publishing parameters."""
    main = ET.fromstring(main_xml)
    definition = main.find('definition')
    if definition is None or definition.get('class') != 'org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition':
        raise RuntimeError('Main job must use a pipeline from SCM')
    scm = definition.find('scm')
    if scm is None or scm.get('class') != 'hudson.plugins.git.GitSCM':
        raise RuntimeError('Main job must use GitSCM')
    branches = [branch.text for branch in scm.findall('branches/hudson.plugins.git.BranchSpec/name')]
    if branches != ['*/main']:
        raise RuntimeError('Main SCM must select only */main')
    if definition.findtext('scriptPath') != 'Jenkinsfile':
        raise RuntimeError('Main SCM must use Jenkinsfile')
    root = ET.Element('flow-definition', {'plugin': 'workflow-job'})
    ET.SubElement(root, 'description').text = (
        'Manual-only MediSur blocking coverage demo. Trusted main SCM; expected '
        'gate failure. No publish/deploy. Docker socket is not a security sandbox.'
    )
    ET.SubElement(root, 'keepDependencies').text = 'false'
    ET.SubElement(root, 'properties')
    demo_definition = copy.deepcopy(definition)
    demo_definition.find('scriptPath').text = DEMO_SCRIPT
    root.append(demo_definition)
    ET.SubElement(root, 'triggers')
    ET.SubElement(root, 'disabled').text = 'false'
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def prepare_job(services):
    pipeline = demo_pipeline_xml(services.jenkins(f'/job/{MAIN_JOB}/config.xml', raw=True))
    try:
        status = services.jenkins(f'/job/{DEMO_JOB}/api/json?tree=inQueue,lastBuild[building]')
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        return pipeline, '/createItem?name=' + DEMO_JOB
    if status.get('inQueue') or (status.get('lastBuild') or {}).get('building'):
        raise RuntimeError('Wait for the demo job to be idle before reconfiguring it')
    existing = ET.fromstring(services.jenkins(f'/job/{DEMO_JOB}/config.xml', raw=True))
    if existing.findtext('definition/scriptPath') != DEMO_SCRIPT:
        raise RuntimeError('Refusing to replace an unrelated job with the demo name')
    return pipeline, f'/job/{DEMO_JOB}/config.xml'


def configure_sonar(services):
    # Read, do not repair or mutate, the gate shared with the main project.
    gate = services.sonar('/api/qualitygates/show?name=' + urllib.parse.quote(GATE_NAME))
    condition = next((item for item in gate.get('conditions', []) if item.get('metric') == 'coverage'), None)
    if condition is None or condition.get('op') != 'LT' or float(condition.get('error', -1)) != 60:
        raise RuntimeError('The existing shared gate must reject overall coverage below 60 percent')
    if not services.sonar('/api/projects/search?projects=' + DEMO_PROJECT).get('components'):
        services.sonar('/api/projects/create', {
            'project': DEMO_PROJECT, 'name': 'MediSur Quality Gate Demo', 'visibility': 'private',
        })
    services.sonar('/api/qualitygates/select', {'projectKey': DEMO_PROJECT, 'gateName': GATE_NAME})
    hooks = services.sonar('/api/webhooks/list?project=' + DEMO_PROJECT).get('webhooks', [])
    hook = next((item for item in hooks if item['name'] == 'Jenkins'), None)
    fields = {
        'name': 'Jenkins', 'url': 'http://jenkins:8080/sonarqube-webhook/',
        'secret': services.secret(WEBHOOK_SECRET),
    }
    if hook:
        fields['webhook'] = hook['key']
        services.sonar('/api/webhooks/update', fields)
    else:
        fields['project'] = DEMO_PROJECT
        services.sonar('/api/webhooks/create', fields)
    # A separate project analysis token cannot analyze the main Sonar project.
    # Reuse the root-protected token on reruns; never overwrite the main token.
    token_path = services.SECRET_DIR / DEMO_TOKEN
    if not token_path.exists():
        token = services.sonar('/api/user_tokens/generate', {
            'name': DEMO_TOKEN + '-' + str(time.time_ns()),
            'type': 'PROJECT_ANALYSIS_TOKEN', 'projectKey': DEMO_PROJECT,
        })['token']
        services.save_secret(DEMO_TOKEN, token)
    if not services.secret(DEMO_TOKEN):
        raise RuntimeError('The protected demo token file is empty')
    token_path.chmod(0o600)


def configure_jenkins(services, pipeline, target):
    payload = base64.b64encode(json.dumps({
        'token': services.secret(DEMO_TOKEN),
        'webhook': services.secret(WEBHOOK_SECRET),
    }).encode()).decode()
    # Secrets travel only in an authenticated loopback POST body. Do not print
    # responses or exception bodies: Jenkins script errors can contain input.
    script = '''
import groovy.json.JsonSlurper
import com.cloudbees.plugins.credentials.*
import com.cloudbees.plugins.credentials.domains.Domain
import org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl
import hudson.util.Secret
def values = new JsonSlurper().parseText(new String(Base64.decoder.decode('PAYLOAD'), 'UTF-8'))
def store = SystemCredentialsProvider.getInstance().getStore()
def shared = store.getCredentials(Domain.global()).find { it.id == 'sonarqube-webhook-secret' }
if (!(shared instanceof StringCredentialsImpl) || shared.secret.plainText != values.webhook) {
  throw new IllegalStateException('Existing shared webhook credential must match the protected secret')
}
def credential = new StringCredentialsImpl(CredentialsScope.GLOBAL, 'sonar-demo-token',
  'Project analysis only: medisur-quality-demo', Secret.fromString(values.token))
def old = store.getCredentials(Domain.global()).find { it.id == 'sonar-demo-token' }
def saved = old ? store.updateCredentials(Domain.global(), old, credential) : store.addCredentials(Domain.global(), credential)
if (!saved) throw new IllegalStateException('Demo credential was not saved')
println('Demo credential configured')
'''.replace('PAYLOAD', payload)
    result = services.jenkins('/scriptText', {'script': script}, raw=True)
    if result.strip() != 'Demo credential configured':
        raise RuntimeError('Jenkins demo credential configuration failed')
    authorization = base64.b64encode(('medisur-admin:' + services.secret('jenkins_api_token')).encode()).decode()
    request = urllib.request.Request(services.JENKINS + target, data=pipeline, headers={
        'Authorization': 'Basic ' + authorization, 'Content-Type': 'application/xml',
    })
    with urllib.request.urlopen(request, timeout=60):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        parser.exit(1, 'Run as root on VM-TOOLS; no services were contacted.\n')
    try:
        services = load_services()
        # Require the existing main setup, rather than generating a new shared secret.
        for name in ('jenkins_api_token', 'sonar_admin_password', WEBHOOK_SECRET):
            if not services.secret(name):
                raise RuntimeError('Required protected secret is empty')
        pipeline, target = prepare_job(services)
        configure_sonar(services)
        configure_jenkins(services, pipeline, target)
    except Exception:
        print('Demo setup failed; inspect service state privately. No build was requested. '
              'Partial configuration may remain; rerun after correcting the cause.', file=sys.stderr)
        return 1
    print('Configured medisur-quality-demo from the main SCM with the existing 60 percent gate. '
          'No triggers installed and no build requested. Run manually only as a controlled test.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
