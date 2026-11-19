"""Local authenticated Jenkins administration; never print credentials."""
import argparse
import base64
import json
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['status', 'build', 'console', 'plugins', 'install-github', 'lint', 'tests', 'delete-demo-job'])
parser.add_argument('--job', default='medisur-main')
parser.add_argument('--build', default='lastBuild')
parser.add_argument('--lines', type=int, default=65)
args = parser.parse_args()
token = Path('/etc/medisur/secrets/jenkins_api_token').read_text().strip()
authorization = 'Basic ' + base64.b64encode(('medisur-admin:' + token).encode()).decode()


def call(path, data=None, content_type=None):
    headers = {'Authorization': authorization}
    if content_type:
        headers['Content-Type'] = content_type
    return urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080' + path, data=data, headers=headers), timeout=90).read().decode()


if args.action == 'status':
    print(call(f'/job/{args.job}/{args.build}/api/json?tree=number,result,building,url,actions[causes[shortDescription]],timestamp,duration'))
elif args.action == 'build':
    call(f'/job/{args.job}/build', data=b'')
    print('Bootstrap build queued')
elif args.action == 'console':
    print('\n'.join(call(f'/job/{args.job}/{args.build}/consoleText').splitlines()[-args.lines:]))
elif args.action == 'plugins':
    data = json.loads(call('/pluginManager/api/json?tree=plugins[shortName,version,active]'))
    print(json.dumps([p for p in data['plugins'] if p['shortName'] in ['github', 'junit', 'sonar', 'workflow-job']], indent=2))
elif args.action == 'tests':
    print(call(f'/job/{args.job}/{args.build}/testReport/api/json?tree=failCount,skipCount,totalCount,passCount'))
elif args.action == 'delete-demo-job':
    # Only the isolated manual demonstration jobs may be removed and recreated.
    if args.job not in ('medisur-rollout-demo', 'medisur-quality-demo'):
        raise SystemExit('Refusing to delete a job that is not an isolated demonstration')
    call(f'/job/{args.job}/doDelete', data=b'')
    print(f'Deleted demonstration job {args.job}')
elif args.action == 'install-github':
    call('/pluginManager/installNecessaryPlugins', data=b'<jenkins><install plugin="github@1.47.0" /></jenkins>', content_type='text/xml')
    print('GitHub plugin installation requested')
elif args.action == 'lint':
    raise SystemExit('Use declarative linter multipart endpoint with the current Jenkinsfile')