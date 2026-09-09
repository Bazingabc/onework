import tempfile
import unittest
from pathlib import Path
from agent_views.bridge.devices import DeviceStore
from agent_views.bridge.service import AgentViewsError


class DeviceMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=DeviceStore(Path(self.temp.name),clock=lambda:1000)

    def pair(self, metadata=None):
        qr=self.store.issue('https://192.168.1.2:8766','a'*64)
        return self.store.begin(qr['code'],'26048RP6AC','t'*43,metadata)['requestId']

    def test_pending_metadata_survives_approval(self):
        device={'category':'tablet','brand':'Redmi','model':'26048RP6AC','productName':'REDMI K Pad 2','androidVersion':'16'}
        identifier=self.pair(device)
        self.assertEqual(self.store.pending()[0]['metadata'],device)
        self.store.approve(identifier,'view')
        self.assertEqual(self.store.devices()[0]['metadata'],device)

    def test_legacy_device_enriches_without_identity_or_role_change(self):
        identifier=self.pair(); self.store.approve(identifier,'view')
        self.store.update_metadata(identifier,{'model':'test','role':'control','deviceId':'another','hash':'fake'})
        result=self.store.devices()[0]
        self.assertEqual(result['metadata'],{'model':'test'})
        self.assertEqual(result['deviceId'],identifier)
        self.assertEqual(self.store.identity('t'*43)['role'],'view')

    def test_revoked_device_cannot_return_via_metadata(self):
        identifier=self.pair(); self.store.approve(identifier,'control'); self.store.revoke(identifier)
        with self.assertRaises(AgentViewsError): self.store.update_metadata(identifier,{'model':'test'})
        self.assertEqual(self.store.devices(),[])

    def test_untrusted_display_fields_are_bounded_and_allowlisted(self):
        result=self.store.metadata({'brand':'A\nB\u202e','model':'x'*1000,'category':'administrator','androidVersion':{'x':'bad'},'token':'secret'})
        self.assertEqual(result,{'brand':'AB','model':'x'*80})
        self.assertEqual(self.store.metadata(['invalid']),{})

    def test_missing_metadata_is_backward_compatible(self):
        identifier=self.pair(); self.store.approve(identifier,'control')
        self.assertEqual(self.store.devices()[0]['metadata'],{})
