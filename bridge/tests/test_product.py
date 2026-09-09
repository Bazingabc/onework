import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from agent_views.bridge.operations import OperationStore
from agent_views.bridge.devices import DeviceStore
from agent_views.bridge.product import ProductRuntime
from agent_views.bridge.service import AgentViewsError, AgentViewsService


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def store(self):
        result=OperationStore(self.path/'ops.db')
        self.addCleanup(result.close)
        return result

    def test_duplicate_is_dispatched_once(self):
        store=self.store(); calls=[]; identifier=str(uuid.uuid4())
        def send(): return store.execute(identifier,'tablet','session:1',{},lambda:None,lambda:calls.append(1) or {'sent':True})
        with ThreadPoolExecutor(max_workers=5) as workers:
            results=list(workers.map(lambda _:send(),range(12)))
        self.assertEqual(calls,[1]); self.assertTrue(all(x=={'sent':True} for x in results))

    def test_uncertain_blocks_new_ids(self):
        store=self.store()
        def fail(): raise TimeoutError()
        with self.assertRaises(AgentViewsError): store.execute(str(uuid.uuid4()),'tablet','session:1',{},lambda:None,fail)
        with self.assertRaises(AgentViewsError) as error: store.execute(str(uuid.uuid4()),'tablet','session:1',{},lambda:None,lambda: self.fail('must not dispatch'))
        self.assertEqual(error.exception.code,'operation_unknown')

    def test_rejected_does_not_lock_target(self):
        store=self.store()
        def fail(): raise AgentViewsError('pending_changed','changed',409)
        with self.assertRaises(AgentViewsError): store.execute(str(uuid.uuid4()),'tablet','session:1',{},lambda:None,fail)
        self.assertEqual(store.execute(str(uuid.uuid4()),'tablet','session:1',{},lambda:None,lambda:{'ok':True}),{'ok':True})

    def test_operation_identity_and_payload_are_bound(self):
        store=self.store(); identifier=str(uuid.uuid4())
        store.execute(identifier,'one','s',{'text':'a'},lambda:None,lambda:{})
        for device,payload in [('two',{'text':'a'}),('one',{'text':'b'})]:
            with self.assertRaises(AgentViewsError): store.execute(identifier,device,'s',payload,lambda:None,lambda:self.fail())
        with self.assertRaises(AgentViewsError): store.inspect(identifier,'two')

    def test_restart_marks_inflight_unknown(self):
        store=OperationStore(self.path/'ops.db')
        with store.db: store.db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)',('id','d','s','hash','dispatching',None,1))
        store.close()
        recovered=self.store()
        self.assertEqual(recovered.inspect('id','d')['state'],'unknown')

    def test_pairing_requires_local_approval_and_recovers_begin(self):
        store=DeviceStore(self.path/'devices',clock=lambda:1000)
        qr=store.issue('https://192.168.1.2:8766','a'*64); token='t'*43
        first=store.begin(qr['code'],'Tablet',token)
        self.assertEqual(first,store.begin(qr['code'],'Tablet',token))
        self.assertIsNone(store.identity(token))
        store.approve(first['requestId'],'view')
        self.assertEqual(store.identity(token)['role'],'view')
        store.set_role(first['requestId'],'control')
        self.assertEqual(store.identity(token)['role'],'control')
        store.revoke(first['requestId']); self.assertIsNone(store.identity(token))

    def test_three_concurrent_device_limit(self):
        store=DeviceStore(self.path/'devices',clock=lambda:1000)
        for number in range(4):
            token=str(number)*43; qr=store.issue('https://192.168.1.2:8766','a'*64)
            identifier=store.begin(qr['code'],'Tablet',token)['requestId']; store.approve(identifier,'control')
            if number<3: self.assertIsNotNone(store.identity(token))
            else:
                with self.assertRaises(AgentViewsError) as error: store.identity(token)
                self.assertEqual(error.exception.code,'device_limit')

    def test_read_only_rejects_without_dispatch(self):
        runtime=ProductRuntime(None,None,self.path)
        self.addCleanup(runtime.operations.close)
        with self.assertRaises(AgentViewsError) as error:
            runtime.action({'role':'view','deviceId':'one'},{'kind':'message','target':'1','payload':{'text':'hello'}})
        self.assertEqual(error.exception.code,'device_read_only')

    def test_invalid_message_not_recorded(self):
        runtime=ProductRuntime(None,None,self.path); self.addCleanup(runtime.operations.close)
        with self.assertRaises(AgentViewsError) as error:
            runtime.action({'role':'control','deviceId':'one'},{'kind':'message','target':'1','operationId':str(uuid.uuid4()),'payload':{'text':''}})
        self.assertEqual(error.exception.code,'message_invalid')
        self.assertEqual(runtime.operations.recent(),[])

    def test_answer_does_not_evict_next_question(self):
        class Protocol:
            def respond_server_request(inner,identifier,result):
                service._pending['s']=replacement
        service=AgentViewsService(Protocol(),workspace=self.path,state_path=self.path/'state.json')
        pending={'request_id':'old','kind':'user_input','params':{'questions':[{'id':'q'}]}}
        replacement={'request_id':'new','kind':'user_input','params':{'questions':[{'id':'next'}]}}
        service._pending['s']=pending
        service._answer_user_input('s',pending,text='answer',answers=None)
        self.assertIs(service._pending['s'],replacement)
