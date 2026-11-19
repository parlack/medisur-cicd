"""Record actual isolated Sonar rejection; run on VM-TOOLS as root."""
import base64
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

secrets = Path('/etc/medisur/secrets')


def get(base, route, username, filename):
    token = (secrets / filename).read_text().strip()
    auth = base64.b64encode((username + ':' + token).encode()).decode()
    request = urllib.request.Request(base + route, headers={'Authorization': 'Basic ' + auth})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


result = {
    'collected_at': datetime.now(timezone.utc).isoformat(),
    'scope': 'Controlled uncovered-code fixture; isolated project; no production changes',
    'build': get('http://127.0.0.1:8080', '/job/medisur-quality-demo/lastBuild/api/json?tree=number,result,building', 'medisur-admin', 'jenkins_api_token'),
    'tests': get('http://127.0.0.1:8080', '/job/medisur-quality-demo/lastBuild/testReport/api/json?tree=passCount,failCount,skipCount', 'medisur-admin', 'jenkins_api_token'),
    'workflow': get('http://127.0.0.1:8080', '/job/medisur-quality-demo/lastBuild/wfapi/describe', 'medisur-admin', 'jenkins_api_token'),
    'sonar': get('http://127.0.0.1:9000', '/api/qualitygates/project_status?projectKey=medisur-quality-demo', 'admin', 'sonar_admin_password'),
}
path = Path('/opt/medisur-cicd/docs/evidence/quality-gate.json')
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(result, indent=2) + '\n')
print('Recorded gate:', result['sonar']['projectStatus']['status'], 'tests passed:', result['tests']['passCount'])