"""Configure medisur-branches on VM-TOOLS; never queue a build or change main.

Run as root with configure-services.py alongside this file. --check is read-only;
--apply creates/updates only the feature job, its Sonar project and credential.
Publish ci/feature.Jenkinsfile in feature branches before sending real pushes.
GitHubPushTrigger polls eligible Git branches; it does not pin a webhook SHA.
Pending pushes can coalesce. Sonar Community results share one feature project.
This job trusts repository writers and the existing Docker-enabled executor.
No plugin install, service restart, Git publication, or merge permission change.
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

JOB = 'medisur-branches'
PROJECT = 'medisur-feature-ci'
TOKEN = 'sonar-feature-token'
SCRIPT = 'ci/feature.Jenkinsfile'
BRANCH = ':origin/(?!main$).*'
GATE = 'MediSur 60 percent'
MARKER = 'Managed by configure-feature-ci.py.'


def load_services():
    spec = importlib.util.spec_from_file_location(
        'feature_services', Path(__file__).with_name('configure-services.py'))
    services = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(services)
    # Administration is strictly loopback, without proxies or redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    urllib.request.install_opener(urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()))
    return services


def pipeline_xml(main_xml):
    main = ET.fromstring(main_xml)
    definition = main.find('definition')
    if definition is None or definition.get('class') != 'org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition':
        raise RuntimeError('Main must be a pipeline from SCM')
    scm = definition.find('scm')
    if scm is None or scm.get('class') != 'hudson.plugins.git.GitSCM':
        raise RuntimeError('Main must use GitSCM')
    if [item.text for item in scm.findall('branches/hudson.plugins.git.BranchSpec/name')] != ['*/main']:
        raise RuntimeError('Main must select */main')
    if definition.findtext('scriptPath') != 'Jenkinsfile':
        raise RuntimeError('Unexpected main script path')
    remotes = scm.findall('userRemoteConfigs/hudson.plugins.git.UserRemoteConfig')
    if len(remotes) != 1 or remotes[0].findtext('name') not in (None, '', 'origin'):
        raise RuntimeError('Expected one origin remote')
    root = ET.Element('flow-definition', {'plugin': 'workflow-job'})
    ET.SubElement(root, 'description').text = (
        MARKER + ' Trusted non-main branches only; GitHub webhook polling. '
        'Shared queue can coalesce pushes; shared feature Sonar history. No deploy.')
    ET.SubElement(root, 'keepDependencies').text = 'false'
    properties = ET.SubElement(root, 'properties')
    ET.SubElement(properties, 'org.jenkinsci.plugins.workflow.job.properties.DisableConcurrentBuildsJobProperty')
    trigger_property = ET.SubElement(properties, 'org.jenkinsci.plugins.workflow.job.properties.PipelineTriggersJobProperty')
    triggers = ET.SubElement(trigger_property, 'triggers')
    trigger = ET.SubElement(triggers, 'com.cloudbees.jenkins.GitHubPushTrigger', {'plugin': 'github'})
    ET.SubElement(trigger, 'spec')
    feature = copy.deepcopy(definition)
    feature.find('scriptPath').text = SCRIPT
    lightweight = feature.find('lightweight')
    if lightweight is None:
        lightweight = ET.SubElement(feature, 'lightweight')
    lightweight.text = 'false'  # Regex BranchSpec needs a real Git checkout.
    feature_scm = feature.find('scm')
    feature_scm.find('branches/hudson.plugins.git.BranchSpec/name').text = BRANCH
    remote = feature_scm.find('userRemoteConfigs/hudson.plugins.git.UserRemoteConfig')
    for name, value in [('name', 'origin'), ('refspec', '+refs/heads/*:refs/remotes/origin/*')]:
        element = remote.find(name)
        if element is None:
            element = ET.SubElement(remote, name)
        element.text = value
    extensions = feature_scm.find('extensions')
    if extensions is not None:
        extensions.clear()  # Do not inherit main-specific checkout restrictions.
    root.append(feature)
    ET.SubElement(root, 'disabled').text = 'false'
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def preflight(services):
    result = services.jenkins('/scriptText', {'script': '''
import jenkins.model.Jenkins
import hudson.plugins.git.BranchSpec
['git','github','workflow-job','workflow-cps','pipeline-model-definition','sonar','junit','timestamper','plain-credentials'].each {
  assert Jenkins.get().pluginManager.getPlugin(it)?.active : 'Required plugin inactive'
}
def branch = new BranchSpec(':origin/(?!main$).*')
assert branch.matches('origin/feature/ci-check')
assert branch.matches('origin/main-fix')
assert !branch.matches('origin/main')
assert !branch.matches('upstream/feature/test')
println('Prerequisites verified')
'''}, raw=True)
    if result.strip() != 'Prerequisites verified':
        raise RuntimeError('Live plugin/BranchSpec checks failed')
    gate = services.sonar('/api/qualitygates/show?name=' + urllib.parse.quote(GATE))
    if not any(item.get('metric') == 'coverage' and item.get('op') == 'LT'
               and float(item.get('error', -1)) == 60 for item in gate.get('conditions', [])):
        raise RuntimeError('Existing gate must reject overall coverage below 60 percent')
    main = services.jenkins('/job/medisur-main/config.xml', raw=True)
    pipeline = pipeline_xml(main)
    try:
        existing = services.jenkins(f'/job/{JOB}/config.xml', raw=True)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        return main, pipeline, '/createItem?name=' + JOB
    root = ET.fromstring(existing)
    if not root.findtext('description', '').startswith(MARKER) or root.findtext('definition/scriptPath') != SCRIPT:
        raise RuntimeError('Refusing to replace an unrelated job')
    status = services.jenkins(f'/job/{JOB}/api/json?tree=inQueue,builds[building]')
    if status.get('inQueue') or any(build.get('building') for build in status.get('builds', [])):
        raise RuntimeError('Feature job must be idle')
    return main, pipeline, f'/job/{JOB}/config.xml'


def configure_sonar(services):
    if not services.sonar('/api/projects/search?projects=' + PROJECT).get('components'):
        services.sonar('/api/projects/create', {
            'project': PROJECT, 'name': 'MediSur Feature CI', 'visibility': 'private'})
    services.sonar('/api/qualitygates/select', {'projectKey': PROJECT, 'gateName': GATE})
    hooks = services.sonar('/api/webhooks/list?project=' + PROJECT).get('webhooks', [])
    hook = next((item for item in hooks if item['name'] == 'Jenkins'), None)
    fields = {'name': 'Jenkins', 'url': 'http://jenkins:8080/sonarqube-webhook/',
              'secret': services.secret('sonarqube-webhook-secret')}
    if hook:
        fields['webhook'] = hook['key']
    else:
        fields['project'] = PROJECT
    services.sonar('/api/webhooks/' + ('update' if hook else 'create'), fields)
    path = services.SECRET_DIR / TOKEN
    if not path.exists():
        token = services.sonar('/api/user_tokens/generate', {
            'name': TOKEN + '-' + str(time.time_ns()),
            'type': 'PROJECT_ANALYSIS_TOKEN', 'projectKey': PROJECT})['token']
        services.save_secret(TOKEN, token)
    path.chmod(0o600)
    if not services.secret(TOKEN):
        raise RuntimeError('Feature token is empty')


def apply(services, pipeline, target):
    configure_sonar(services)
    payload = base64.b64encode(json.dumps({
        'token': services.secret(TOKEN),
        'webhook': services.secret('sonarqube-webhook-secret')}).encode()).decode()
    script = '''
import groovy.json.JsonSlurper
import com.cloudbees.plugins.credentials.*
import com.cloudbees.plugins.credentials.domains.Domain
import org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl
import hudson.util.Secret
def values = new JsonSlurper().parseText(new String(Base64.decoder.decode('PAYLOAD'), 'UTF-8'))
def store = SystemCredentialsProvider.getInstance().getStore()
def shared = store.getCredentials(Domain.global()).find { it.id == 'sonarqube-webhook-secret' }
assert shared instanceof StringCredentialsImpl
assert shared.secret.plainText == values.webhook
def old = store.getCredentials(Domain.global()).find { it.id == 'sonar-feature-token' }
assert old == null || old instanceof StringCredentialsImpl
def credential = new StringCredentialsImpl(CredentialsScope.GLOBAL, 'sonar-feature-token',
  'Project analysis only: medisur-feature-ci', Secret.fromString(values.token))
assert old ? store.updateCredentials(Domain.global(), old, credential) : store.addCredentials(Domain.global(), credential)
println('Feature credential configured')
'''.replace('PAYLOAD', payload)
    if services.jenkins('/scriptText', {'script': script}, raw=True).strip() != 'Feature credential configured':
        raise RuntimeError('Credential configuration failed')
    authorization = base64.b64encode(('medisur-admin:' + services.secret('jenkins_api_token')).encode()).decode()
    request = urllib.request.Request(services.JENKINS + target, data=pipeline, headers={
        'Authorization': 'Basic ' + authorization, 'Content-Type': 'application/xml'})
    with urllib.request.urlopen(request, timeout=60):
        pass
    result = services.jenkins('/scriptText', {'script': '''
import jenkins.model.Jenkins
import jenkins.triggers.SCMTriggerItem
import com.cloudbees.jenkins.GitHubPushTrigger
def job = Jenkins.get().getItemByFullName('medisur-branches')
assert job.getTriggers().values().any { it instanceof GitHubPushTrigger }
def scms = SCMTriggerItem.SCMTriggerItems.asSCMTriggerItem(job).getSCMs()
assert scms.size() == 1
assert scms[0].branches*.name == [':origin/(?!main$).*']
assert !job.isInQueue()
println('Feature trigger and SCM verified; no build queued')
'''}, raw=True)
    if result.strip() != 'Feature trigger and SCM verified; no build queued':
        raise RuntimeError('Live trigger/SCM verification failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        parser.exit(1, 'Run as root on VM-TOOLS. No services contacted.\n')
    phase = 'preflight'
    try:
        services = load_services()
        original, pipeline, target = preflight(services)
        phase = 'declarative validation'
        jenkinsfile = Path(__file__).with_name('feature.Jenkinsfile')
        if not jenkinsfile.exists():
            jenkinsfile = Path(__file__).parent.parent / SCRIPT
        body = urllib.parse.urlencode({'jenkinsfile': jenkinsfile.read_text()}).encode()
        auth = base64.b64encode(('medisur-admin:' + services.secret('jenkins_api_token')).encode()).decode()
        request = urllib.request.Request(services.JENKINS + '/pipeline-model-converter/validate', data=body,
            headers={'Authorization': 'Basic ' + auth,
                     'Content-Type': 'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(request, timeout=60) as response:
            validation = response.read().decode()
            if 'Jenkinsfile successfully validated.' not in validation:
                # The linter received source only, not credential values.
                print(validation[:6000], file=sys.stderr)
                raise RuntimeError('Feature Jenkinsfile failed live declarative validation')
        if args.apply:
            phase = 'feature configuration'
            apply(services, pipeline, target)
        phase = 'main integrity check'
        if services.jenkins('/job/medisur-main/config.xml', raw=True) != original:
            raise RuntimeError('Main configuration changed during this operation')
    except Exception:
        # API exception bodies may contain credentials: do not print them.
        print(f'Failed during {phase}; inspect privately. Partial setup may remain. '
              'No build was requested.', file=sys.stderr)
        return 1
    print(('Configured medisur-branches.' if args.apply else 'Read-only checks passed.')
          + ' Main configuration unchanged; no build requested. '
          'Real push verification awaits publication of ci/feature.Jenkinsfile.')
    return 0


if __name__ == '__main__':
    sys.exit(main())