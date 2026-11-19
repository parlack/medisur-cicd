"""Linux-only focused tests, using synthetic values and mocked Jenkins I/O."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parent / 'renew-cluster-credentials.py'
if not source.exists():
    source = Path(__file__).resolve().parents[2] / 'scripts' / 'renew-cluster-credentials.py'
spec = importlib.util.spec_from_file_location('renew', source)
renew = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renew)


class RenewalTests(unittest.TestCase):
    def token(self, lifetime=86400, account='jenkins-deployer'):
        data = {'exp': time.time() + lifetime, 'sub': 'system:serviceaccount:medisur:' + account}
        return 'header.' + base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=') + '.signature'

    def test_expiry(self):
        self.assertGreater(renew.token_expiry(self.token(), 'jenkins-deployer'), time.time())

    def test_short_token_rejected(self):
        with self.assertRaises(ValueError):
            renew.token_expiry(self.token(10), 'jenkins-deployer')

    def test_wrong_subject_rejected(self):
        with self.assertRaises(ValueError):
            renew.token_expiry(self.token(), 'prometheus-tools')

    def test_wrong_envelope_rejected(self):
        with self.assertRaises(ValueError):
            renew.validate({'unexpected': 'data'})

    def test_inode_preserved_and_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'secret'
            path.write_text('a longer old synthetic secret')
            before = path.stat().st_ino
            renew.write_in_place(path, 'new', os.getuid(), 0o600)
            self.assertEqual(path.stat().st_ino, before)
            self.assertEqual(path.read_text(), 'new')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'original'
            original.write_text('original')
            link = Path(directory) / 'link'
            link.symlink_to(original)
            with self.assertRaises(OSError):
                renew.write_in_place(link, 'new', os.getuid(), 0o600)
            self.assertEqual(original.read_text(), 'original')

    def test_jenkins_credential_only(self):
        with patch.object(renew, 'jenkins_script', return_value='MEDISUR_CREDENTIAL_UPDATED') as request:
            renew.update_jenkins('synthetic')
        script = request.call_args.args[0]
        self.assertIn('medisur-kubeconfig', script)
        for forbidden in ('createItem', 'config.xml', 'setLabelString', 'Jenkins.get().save'):
            self.assertNotIn(forbidden, script)

    def test_jenkins_http200_error_rejected(self):
        with patch.object(renew, 'jenkins_script', return_value='script failed'):
            with self.assertRaises(RuntimeError):
                renew.update_jenkins('synthetic')


if __name__ == '__main__':
    unittest.main()