"""Portable reference-update checks; synthetic data and temporary state only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import threshold_update as t

class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.prov={'description':'Synthetic test reference','requires_partial_acknowledgement':False}
        self.rows=[['ST100001','AN100001','datatable',0,10,20,30],['ST100001','AN100001','mwtab',100,90,80,70],['ST100002','AN100002','untarg_data',None,50,50,50]]
    def tearDown(self):self.temp.cleanup()
    def candidate(self,partial=False):
        ident=t.prepare('2020-01-01','2020-01-02','selected_range',self.root);p=t.jobpath(ident,self.root)
        prov={**self.prov,'requires_partial_acknowledgement':partial}
        r=t.build_reference(self.rows,ident,prov);t.atomic(p/'candidate.json',{'reference':r,'sha256':t.digest(r),'rows':self.rows});t.update_status(p,state='review')
        return ident,r
    def test_original_r1_exact(self):self.assertEqual(t.build_reference(t.baseline_rows(),'R1',self.prov)['sensitivity'],t.baseline()['sensitivity'])
    def test_default_is_r1(self):self.assertEqual(t.active(self.root),t.baseline())
    def test_linear_quantile_and_null_denominator(self):
        r=t.build_reference(self.rows,'synthetic',self.prov);self.assertEqual(r['sensitivity']['2.5']['cutoffs']['missingness'],2.5);self.assertEqual(r['n_matrices_with_a_value']['missingness'],2)
    def test_ties_strictly_below(self):
        r=t.build_reference([['ST100001','AN100001','mwtab',50,50,50,50]],'synthetic',self.prov);self.assertEqual(r['sensitivity']['10']['matrices_with_an_alert'],0)
    def test_no_duplicates(self):
        with self.assertRaises(ValueError):t.build_reference(self.rows+self.rows[:1],'synthetic',self.prov)
    def test_empty_metric_refused(self):
        with self.assertRaises(ValueError):t.build_reference([['ST100001','AN100001','mwtab',None,50,50,50]],'synthetic',self.prov)
    def test_candidate_does_not_activate(self):self.candidate();self.assertEqual(t.active(self.root)['reference_version'],'R1')
    def test_approval_and_restore(self):
        ident,r=self.candidate();t.approve(ident,t.digest(r),root=self.root);self.assertEqual(t.active(self.root),r);t.restore(self.root);self.assertEqual(t.active(self.root),t.baseline());self.assertEqual(len(list((self.root/'history').glob('*.json'))),2)
    def test_wrong_review_digest_rejected(self):
        ident,r=self.candidate()
        with self.assertRaises(ValueError):t.approve(ident,'0'*64,root=self.root)
        self.assertEqual(t.active(self.root)['reference_version'],'R1')
    def test_partial_requires_acknowledgement(self):
        ident,r=self.candidate(True)
        with self.assertRaises(ValueError):t.approve(ident,t.digest(r),root=self.root)
        t.approve(ident,t.digest(r),True,self.root);self.assertEqual(t.active(self.root),r)
    def test_tampered_scores_rejected(self):
        ident,r=self.candidate();p=t.jobpath(ident,self.root)/'candidate.json';c=t.read(p);c['rows'][0][3]=90;t.atomic(p,c)
        with self.assertRaises(ValueError):t.approve(ident,t.digest(r),root=self.root)
    def test_corrupt_active_never_false_r1(self):
        t.atomic(self.root/'active.json',{'sha256':'bad','reference':t.baseline()})
        with self.assertRaises(ValueError):t.active(self.root)
        t.restore(self.root);self.assertEqual(t.active(self.root),t.baseline())
    def test_date_validation(self):
        for a,b in [('2020-02-02','2020-01-01'),('2020-02-30','2020-03-01'),('2999-01-01','2999-01-01')]:
            with self.assertRaises(ValueError):t.dates(a,b)
    def test_release_inclusive_public_only(self):
        pub=[{'study_id':f'ST10000{i}','analysis_id':f'AN10000{i}'} for i in range(1,5)]
        summary=[{'study_id':'ST100001','release_date':'2020-01-01','submission_date':'1999-01-01'}, {'study_id':'ST100002','release_date':'2020-01-02'}, {'study_id':'ST100003','release_date':'2999-01-01'}, {'study_id':'ST100004','release_date':''}, {'study_id':'ST100005','release_date':'2020-01-01'}]
        rows,excluded,total=t.select_cohort(pub,summary,'2020-01-01','2020-01-02',today='2020-01-02')
        self.assertEqual([x['study_id'] for x in rows],['ST100001','ST100002']);self.assertEqual(total,4);self.assertEqual(len(excluded),2)
    def test_conflicting_dates_excluded(self):
        rows,ex,_=t.select_cohort([{'study_id':'ST100001','analysis_id':'AN100001'}],[{'study_id':'ST100001','release_date':'2020-01-01'},{'study_id':'ST100001','release_date':'2020-01-02'}],'2020-01-01','2020-01-02');self.assertEqual(rows,[]);self.assertEqual(len(ex),1)
    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):t.jobpath('../../elsewhere',self.root)
    def test_invalid_and_nonmonotone_reference(self):
        r=t.baseline();r['sensitivity']['2.5']['cutoffs']['outliers']=101
        with self.assertRaises(ValueError):t.validate_reference(r)
    def test_compact_rows_no_measurements(self):
        v={'study_id':'ST100001','analyses':[{'analysis_id':'AN100001','endpoints':{'mwtab':{'roster':[['private sample']], 'sections':{'analytical':{'sample_missingness':{'score':90}}}}}}]}
        rows=t.rows_from_record(v);self.assertEqual(rows,[['ST100001','AN100001','mwtab',90,None,None,None]]);self.assertNotIn('private sample',json.dumps(rows))
    def test_resume_and_owned_scratch_cleanup(self):
        ident=t.prepare('2020-01-01','2020-01-02','selected_range',self.root);p=t.jobpath(ident,self.root)
        studies=[{'study_id':f'ST10000{i}','release_date':'2020-01-01'} for i in (1,2)]
        t.atomic(p/'cohort.json',{'studies':studies,'discovery_errors':[]});t.update_status(p,state='ready',unknown_dates=0)
        scratch=p/'work/ST100001';scratch.mkdir(parents=True);(scratch/'stale-input.txt').write_text('never reuse an incomplete input')
        calls=[]
        def worker(command,*a,**kw):
            sid=command[command.index('--study')+1];scratch=Path(command[command.index('--run-root')+1]);calls.append(sid)
            self.assertFalse((scratch/'stale-input.txt').exists())
            (scratch/'raw-table.txt').write_text('temporary measurements')
            t.atomic(scratch/'compact_result.json',{'study_id':sid,'rows':[[sid,'AN100001','mwtab',50,50,50,50]],'errors':[]})
            if len(calls)==1:t.atomic(p/'pause',{})
            return {'state':'complete'}
        with patch('native_resources.run_isolated',side_effect=worker):
            t.run(ident,self.root);self.assertEqual(t.read(p/'status.json')['state'],'paused');self.assertEqual(list((p/'work').glob('*')),[])
            t.run(ident,self.root)
        self.assertEqual(calls,['ST100001','ST100002']);self.assertEqual(t.read(p/'status.json')['state'],'review');self.assertEqual(list((p/'work').glob('*')),[])
        self.assertNotIn('temporary measurements',json.dumps(t.read(p/'scores.json')))
    def test_extend_r1_deduplicates_without_replacing(self):
        ident=t.prepare('2020-01-01','2020-01-02','extend_r1',self.root);p=t.jobpath(ident,self.root)
        old=t.baseline_rows()[0];sid=old[0]
        t.atomic(p/'cohort.json',{'studies':[{'study_id':sid,'release_date':'2020-01-01'}],'discovery_errors':[]});t.update_status(p,state='ready',unknown_dates=0)
        t.atomic(p/'records'/(sid+'.json'),{'study_id':sid,'rows':[[*old[:3],0,0,0,0]],'errors':[]})
        t.run(ident,self.root);c=t.read(p/'candidate.json')['reference']
        self.assertEqual(c['reference_matrices'],11749);self.assertEqual(c['sensitivity'],t.baseline()['sensitivity']);self.assertEqual(c['update_provenance']['duplicate_baseline_matrices_not_added'],1)
    def test_changed_code_cannot_mix_reference_runs(self):
        ident=t.prepare('2020-01-01','2020-01-02','selected_range',self.root);p=t.jobpath(ident,self.root)
        t.atomic(p/'cohort.json',{'studies':[{'study_id':'ST100001'}],'discovery_errors':[]});t.update_status(p,state='paused',runtime_sha256='old-code')
        t.run(ident,self.root);self.assertEqual(t.read(p/'status.json')['state'],'failed');self.assertEqual(t.active(self.root)['reference_version'],'R1')
    def test_only_owned_orphan_worker_is_stopped(self):
        import subprocess,sys
        scratch=self.root/'owned';scratch.mkdir()
        proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)','matrix_worker.py','--run-root',str(scratch)])
        try:
            self.assertTrue(t.work_in_use(scratch));t.stop_owned_work(scratch);self.assertLess(proc.wait(timeout=5),0)
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
    def test_reused_pid_does_not_block_resume(self):
        import os
        ident=t.prepare('2020-01-01','2020-01-02','selected_range',self.root)
        t.update_status(t.jobpath(ident,self.root),state='running',pid=os.getpid())
        self.assertEqual(t.status(ident,self.root)['state'],'interrupted')

if __name__=='__main__':unittest.main(verbosity=2)
