"""Offline configuration regression tests; no network calls or credentials."""
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

SPEC = importlib.util.spec_from_file_location('feature', Path(__file__).with_name('configure-feature-ci.py'))
feature = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feature)

MAIN = '''<flow-definition><definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition">
<scm class="hudson.plugins.git.GitSCM"><userRemoteConfigs><hudson.plugins.git.UserRemoteConfig>
<url>https://github.com/example/repo.git</url><credentialsId>github-scm</credentialsId>
</hudson.plugins.git.UserRemoteConfig></userRemoteConfigs>
<branches><hudson.plugins.git.BranchSpec><name>*/main</name></hudson.plugins.git.BranchSpec></branches>
<extensions><restrict-main/></extensions></scm><scriptPath>Jenkinsfile</scriptPath><lightweight>true</lightweight>
</definition><triggers><unexpected/></triggers><properties><publish-parameter/></properties></flow-definition>'''


class FeatureConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.root = ET.fromstring(feature.pipeline_xml(MAIN))

    def test_regex_and_full_fetch(self):
        self.assertEqual(self.root.findtext('.//hudson.plugins.git.BranchSpec/name'), feature.BRANCH)
        self.assertEqual(self.root.findtext('.//refspec'), '+refs/heads/*:refs/remotes/origin/*')

    def test_preserves_repository_credential(self):
        self.assertEqual(self.root.findtext('.//credentialsId'), 'github-scm')
        self.assertEqual(self.root.findtext('.//url'), 'https://github.com/example/repo.git')

    def test_script_and_heavyweight_checkout(self):
        self.assertEqual(self.root.findtext('definition/scriptPath'), 'ci/feature.Jenkinsfile')
        self.assertEqual(self.root.findtext('definition/lightweight'), 'false')

    def test_trigger_and_serialization_present_before_first_build(self):
        self.assertIsNotNone(self.root.find('.//com.cloudbees.jenkins.GitHubPushTrigger'))
        self.assertIsNotNone(self.root.find('.//org.jenkinsci.plugins.workflow.job.properties.DisableConcurrentBuildsJobProperty'))
        self.assertIsNone(self.root.find('.//unexpected'))
        self.assertIsNone(self.root.find('.//publish-parameter'))

    def test_rejects_changed_main(self):
        with self.assertRaises(RuntimeError):
            feature.pipeline_xml(MAIN.replace('*/main', '**'))

    def test_rejects_wrong_definition(self):
        with self.assertRaises(RuntimeError):
            feature.pipeline_xml(MAIN.replace('CpsScmFlowDefinition', 'CpsFlowDefinition'))

    def test_pipeline_has_no_deploy_or_demo_bypass(self):
        source = (Path(__file__).parent.parent / feature.SCRIPT).read_text()
        for forbidden in ['--cov-fail-under=0', 'demo-uncovered', 'docker push', 'scripts/deploy.sh',
                          'medisur-kubeconfig', '--target runtime', 'sonar.branch.name']:
            self.assertNotIn(forbidden, source)
        for required in ['sonar-feature-token', 'sonar.projectKey=medisur-feature-ci',
                         'waitForQualityGate abortPipeline: true', 'githubPush()']:
            self.assertIn(required, source)


if __name__ == '__main__':
    unittest.main()