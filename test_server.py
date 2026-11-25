import os, tempfile, unittest
from pathlib import Path
import server

class TestPanel(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        server.DB=str(Path(self.temp.name)/"test.sqlite3")
        server.init()
    def tearDown(self): self.temp.cleanup()
    def send(self,kind,rev,eid=None,**kw):
        return server.apply({"event_id":eid or f"{kind}:{rev}:{kw.get('row_id','')}", "batch_id":"b1","type":kind,"revision":rev,**kw})
    def start(self,total=2): self.send("batch.started",1,name="DDA teste.xlsx",total=total)
    def row(self,rev=2,rid="r1",amount=28719,eid=None):
        return self.send("row.upsert",rev,eid,row_id=rid,row={"operacao":"PAJ","fornecedor":"Fornecedor teste","documento":"123","valor_centavos":amount,"status":"REVISAR","links":[]})
    def test_rows_arrive_individually(self):
        self.start()
        self.row()
        self.assertEqual(len(server.snapshot()["rows"]),1)
        self.row(rid="r2")
        self.assertEqual(len(server.snapshot()["rows"]),2)
        self.send("batch.completed",3,total=2)
        self.assertEqual(server.snapshot()["batches"][0]["status"],"completed")
    def test_no_premature_completion(self):
        self.start()
        self.row()
        with self.assertRaises(ValueError): self.send("batch.completed",3,total=2)
    def test_replay_and_stale_revision(self):
        self.start()
        self.row(eid="same")
        self.assertTrue(self.row(eid="same")["duplicate"])
        self.row(3,amount=100)
        self.assertTrue(self.row(2,eid="late")["stale"])
        self.assertEqual(server.snapshot()["rows"][0]["valor_centavos"],100)
    def test_shared_review_and_evidence_changes(self):
        self.start()
        self.row()
        server.review("b1","r1",2,True,"keile")
        self.assertTrue(server.snapshot()["rows"][0]["reviewed"])
        self.row(3)
        self.assertTrue(server.snapshot()["rows"][0]["reviewed"])
        self.row(4,amount=100)
        self.assertFalse(server.snapshot()["rows"][0]["reviewed"])
        with self.assertRaises(ValueError): server.review("b1","r1",3,True,"outro")
        server.review("b1","r1",4,True,"outro")
        self.assertEqual(server.snapshot()["rows"][0]["review_actor"],"outro")
    def test_duplicate_upload(self):
        content=b"PK\x03\x04synthetic"
        a=server.enqueue("a.xlsx",content)
        b=server.enqueue("b.xlsx",content)
        self.assertEqual(a["id"],b["id"])
        self.assertTrue(b["duplicate"])
    def test_invalid_amount(self):
        self.start()
        with self.assertRaises(ValueError): self.row(amount=-1)
    def test_link_allowlist(self):
        self.assertEqual(server.safe_links([{"url":"javascript:alert(1)"}]),[])
if __name__=="__main__": unittest.main()
