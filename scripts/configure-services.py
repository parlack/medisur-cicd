"""Run as root on VM-TOOLS; credentials never appear in output or arguments."""
import base64
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

SECRET_DIR = Path('/etc/medisur/secrets')
SONAR = 'http://127.0.0.1:9000'
JENKINS = 'http://127.0.0.1:8080'
REPOSITORY = 'SergioMultimedios/medisur-cicd'


def secret(name):
    return (SECRET_DIR / name).read_text().strip()


def save_secret(name, value):
    path = SECRET_DIR / name
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(value)


def request(base, path, username, password, data=None, raw=False, headers=None):
    auth = base64.b64encode(f'{username}:{password}'.encode()).decode()
    actual_headers = {'Authorization': 'Basic ' + auth}
    actual_headers.update(headers or {})
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    response = urllib.request.urlopen(
        urllib.request.Request(base + path, data=body, headers=actual_headers), timeout=90
    ).read()
    return response.decode() if raw else json.loads(response or '{}')


def sonar(path, data=None):
    return request(SONAR, path, 'admin', secret('sonar_admin_password'), data)


def configure_sonar():
    project = 'medisur-cicd'
    if not sonar('/api/projects/search?projects=' + project).get('components'):
        sonar('/api/projects/create', {'project': project, 'name': 'MediSur CI/CD', 'visibility': 'private'})
    gates = sonar('/api/qualitygates/list').get('qualitygates', [])
    gate = next((gate for gate in gates if gate['name'] == 'MediSur 60 percent'), None)
    if gate is None:
        gate = sonar('/api/qualitygates/create', {'name': 'MediSur 60 percent'})
    existing = sonar('/api/qualitygates/show?name=' + urllib.parse.quote(gate['name'])).get('conditions', [])
    desired = [('coverage', 'LT', '60'), ('bugs', 'GT', '0'), ('vulnerabilities', 'GT', '0')]
    for metric, operation, threshold in desired:
        current = next((condition for condition in existing if condition.get('metric') == metric), None)
        if current is None:
            sonar('/api/qualitygates/create_condition', {'gateName': gate['name'], 'metric': metric, 'op': operation, 'error': threshold})
        elif current.get('op') != operation or str(current.get('error')) != threshold:
            sonar('/api/qualitygates/update_condition', {'id': current['id'], 'metric': metric, 'op': operation, 'error': threshold})
    sonar('/api/qualitygates/select', {'projectKey': project, 'gateName': gate['name']})
    if not (SECRET_DIR / 'sonar-token').exists():
        token = sonar('/api/user_tokens/generate', {'name': 'medisur-jenkins-' + str(int(time.time())), 'type': 'PROJECT_ANALYSIS_TOKEN', 'projectKey': project})['token']
        save_secret('sonar-token', token)
    if not (SECRET_DIR / 'sonarqube-webhook-secret').exists():
        save_secret('sonarqube-webhook-secret', secrets.token_hex(32))
    hooks = sonar('/api/webhooks/list?project=' + project).get('webhooks', [])
    hook = next((hook for hook in hooks if hook['name'] == 'Jenkins'), None)
    fields = {'name': 'Jenkins', 'url': 'http://jenkins:8080/sonarqube-webhook/', 'secret': secret('sonarqube-webhook-secret')}
    if hook:
        fields['webhook'] = hook['key']
        sonar('/api/webhooks/update', fields)
    else:
        fields['project'] = project
        sonar('/api/webhooks/create', fields)
    print('Sonar project, blocking gate and authenticated webhook configured.')


def jenkins(path, data=None, raw=False):
    return request(JENKINS, path, 'medisur-admin', secret('jenkins_api_token'), data, raw)


def configure_jenkins():
    credentials = [
        ('username', 'github-scm', 'SergioMultimedios', secret('github-token')),
        ('username', 'ghcr', 'SergioMultimedios', secret('github-token')),
        ('text', 'sonar-token', '', secret('sonar-token')),
        ('text', 'sonarqube-webhook-secret', '', secret('sonarqube-webhook-secret')),
        ('file', 'medisur-kubeconfig', 'kubeconfig', secret('deployer-kubeconfig')),
    ]
    # Values travel in an authenticated loopback request, never process argv/logs.
    payload = base64.b64encode(json.dumps(credentials).encode()).decode()
    script = '''
import jenkins.model.Jenkins
import groovy.json.JsonSlurper
import com.cloudbees.plugins.credentials.*
import com.cloudbees.plugins.credentials.domains.Domain
import com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl
import org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl
import org.jenkinsci.plugins.plaincredentials.impl.FileCredentialsImpl
import hudson.util.Secret
import com.cloudbees.plugins.credentials.SecretBytes
def rows = new JsonSlurper().parseText(new String(Base64.decoder.decode('PAYLOAD'), 'UTF-8'))
def store = SystemCredentialsProvider.getInstance().getStore()
rows.each { row ->
  def credential
  if (row[0] == 'username') credential = new UsernamePasswordCredentialsImpl(CredentialsScope.GLOBAL, row[1], row[1], row[2], row[3])
  else if (row[0] == 'text') credential = new StringCredentialsImpl(CredentialsScope.GLOBAL, row[1], row[1], Secret.fromString(row[3]))
  else credential = new FileCredentialsImpl(CredentialsScope.GLOBAL, row[1], row[1], row[2], SecretBytes.fromBytes(row[3].getBytes('UTF-8')))
  def old = store.getCredentials(Domain.global()).find { it.id == row[1] }
  if(old) store.updateCredentials(Domain.global(), old, credential)
  else store.addCredentials(Domain.global(), credential)
}
Jenkins.get().setLabelString('medisur-ci')
Jenkins.get().save()
println('Credentials updated')
'''.replace('PAYLOAD', payload)
    result = jenkins('/scriptText', {'script': script}, raw=True)
    if result.strip() != 'Credentials updated':
        raise RuntimeError('Jenkins credential setup failed; inspect server privately.')
    pipeline = f'''<?xml version="1.0" encoding="UTF-8"?>
<flow-definition plugin="workflow-job">
<description>MediSur: webhook CI/CD. Trusted repository contributors only.</description>
<keepDependencies>false</keepDependencies>
<properties/>
<definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition" plugin="workflow-cps">
 <scm class="hudson.plugins.git.GitSCM" plugin="git">
 <configVersion>2</configVersion>
 <userRemoteConfigs><hudson.plugins.git.UserRemoteConfig>
 <url>https://github.com/{escape(REPOSITORY)}.git</url><credentialsId>github-scm</credentialsId>
 </hudson.plugins.git.UserRemoteConfig></userRemoteConfigs>
 <branches><hudson.plugins.git.BranchSpec><name>*/main</name></hudson.plugins.git.BranchSpec></branches>
 <doGenerateSubmoduleConfigurations>false</doGenerateSubmoduleConfigurations><submoduleCfg class="empty-list"/><extensions/>
 </scm><scriptPath>Jenkinsfile</scriptPath><lightweight>true</lightweight>
</definition>
<triggers><com.cloudbees.jenkins.GitHubPushTrigger plugin="github"><spec/></com.cloudbees.jenkins.GitHubPushTrigger></triggers>
<disabled>false</disabled>
</flow-definition>'''
    auth = base64.b64encode(('medisur-admin:' + secret('jenkins_api_token')).encode()).decode()
    try:
        jenkins('/job/medisur-main/api/json')
        target = '/job/medisur-main/config.xml'
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        target = '/createItem?name=medisur-main'
    urllib.request.urlopen(urllib.request.Request(JENKINS + target, data=pipeline.encode(), headers={'Authorization': 'Basic ' + auth, 'Content-Type': 'application/xml'}), timeout=60).close()
    print('Jenkins SCM pipeline configured, no build manually triggered.')


if __name__ == '__main__':
    configure_sonar()
    configure_jenkins()