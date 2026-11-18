"""API-level integration test for the dual-site sync drill (stdlib + fastapi in /tmp/pylibs).

Uses an in-memory SQLite database (StaticPool so every request shares one
connection) and FastAPI TestClient, exercising the full stack: routes, pydantic
validation, JWT auth, audit middleware and the JSON state model.
Run: python3 -I test_artifacts/gb-4106/test_sync_api.py
"""
import os,pathlib,sys
sys.path.insert(0,'/tmp/pylibs')  # isolated (-I) mode ignores PYTHONPATH
os.environ['DATABASE_URL']='sqlite://'  # in-memory; configured below with StaticPool
os.environ['JWT_SECRET']='test-secret'
os.environ['SEED_DEMO']='false'
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'backend'))
fails=[]
def check(name,cond,detail=''):
    print(('PASS' if cond else 'FAIL'),name,detail)
    if not cond:fails.append(name)

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
import src.config.database as database
database.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
database.Session.configure(bind=database.engine)

from fastapi.testclient import TestClient
from src.config.database import Base,Session
from src.models.user import User
from src.utils.password import hash_password
from src.main import app
with Session() as db:
    Base.metadata.create_all(database.engine)
    db.add(User(email='operator@beiling.test',role='operator',password_hash=hash_password('EdgeStore!17')))
    db.add(User(email='viewer@beiling.test',role='viewer',password_hash=hash_password('EdgeStore!17')))
    db.commit()

c=TestClient(app)
token=lambda role:c.post('/auth/login',json={'email':f'{role}@beiling.test','password':'EdgeStore!17'}).json()['token']
H=lambda role:{'Authorization':'Bearer '+token(role)}

# viewer cannot create (403)
r=c.post('/sync-drills',json={'name':'权限测试','policy':'lww'},headers=H('viewer'))
check('viewer create 403',r.status_code==403,str(r.status_code))

# operator creates a drill
r=c.post('/sync-drills',json={'name':'断网收敛端到端','description':'api 集成','policy':'lww'},headers=H('operator'))
check('create 201',r.status_code==201,str(r.status_code));d=r.json();did=d['id']
check('starts online and converged',d['online'] and d['converged'] and d['backlog']==0)

# invalid policy rejected by pydantic
r=c.post('/sync-drills',json={'name':'坏策略','policy':'quorum'},headers=H('operator'))
check('bad policy 422',r.status_code==422)

def write(site,op,key,value=None):return c.post(f'/sync-drills/{did}/writes',json={'site':site,'op':op,'key':key,'value':value},headers=H('operator'))
def link(online):return c.post(f'/sync-drills/{did}/link',json={'online':online},headers=H('operator'))
def sync(limit=3):return c.post(f'/sync-drills/{did}/sync',json={'batch_limit':limit},headers=H('operator')).json()
def detail():return c.get(f'/sync-drills/{did}',headers=H('operator')).json()

# --- diverge offline: each site reads/writes only its own kv ---
link(False)
write('A','put','k1','a-old');write('A','delete','k2')
write('B','put','k1','b-concurrent');write('B','put','k2','late-old-put');write('B','put','k3','b3')
d=detail()
check('offline divergence visible',d['sites']['A']['kv']=={'k1':'a-old'} and d['sites']['B']['kv']=={'k1':'b-concurrent','k2':'late-old-put','k3':'b3'})
check('backlog counted',d['backlog']==5,str(d['backlog']))

# sync while down is blocked
r=sync()
check('sync blocked while offline',r['report']['blocked'] and r['report']['exchanged']==0)

# --- half-sync then disconnect again, then resume: no duplicate application ---
link(True)
r1=sync(2)  # 2 B->A then 2 A->B
check('partial batch remains',not r1['report']['completed'] and r1['report']['exchanged']==4,str(r1['report']))
link(False)
r=sync()
check('mid-sync disconnect blocks',r['report']['blocked'])
before=detail();counts_before=(before['sites']['A']['op_count'],before['sites']['B']['op_count'])
link(True)
last=None
for _ in range(10):
    last=sync(2)
    if last['report']['completed']:break
check('eventually converges',last['report']['completed'],str(last['report']))
d=detail()
check('sites converge to identical kv',d['invariants']['ok'] and d['invariants']['converged'])
# k1: A-1 vs B-1 equal clocks -> origin B wins; k2: A-2 delete vs B-2 put equal clocks -> lww tie-break origin B (put)
expected={'k1':'b-concurrent','k2':'late-old-put','k3':'b3'}
check('deterministic tie-break convergence',d['sites']['A']['kv']==d['sites']['B']['kv']==expected,str(d['sites']['A']['kv']))
check('conflict decisions recorded',len(d['conflicts'])>=2)

# idempotency: extra syncs and the backlog being empty change nothing
snap=detail()['sites']['A']['op_count']
for _ in range(3):sync(5)
after=detail()
check('retry syncs do not re-apply',after['sites']['A']['op_count']==snap and after['invariants']['ok'])
check('no duplicate op ids invariant',after['invariants']['problems']==[])

# --- strict resurrection case: a strictly newer tombstone beats a late old put ---
r=c.post('/sync-drills',json={'name':'墓碑复活回归','policy':'lww'},headers=H('operator'));did2=r.json()['id']
def w2(payload):return c.post(f'/sync-drills/{did2}/writes',json=payload,headers=H('operator'))
w2({'site':'A','op':'put','key':'x','value':'shared-old'})
c.post(f'/sync-drills/{did2}/sync',json={'batch_limit':10},headers=H('operator'))
c.post(f'/sync-drills/{did2}/link',json={'online':False},headers=H('operator'))
w2({'site':'A','op':'delete','key':'x'})            # A clock 2
w2({'site':'B','op':'put','key':'x','value':'stale'})  # B clock 2 (tie)
c.post(f'/sync-drills/{did2}/link',json={'online':True},headers=H('operator'))
for _ in range(10):
    rr=c.post(f'/sync-drills/{did2}/sync',json={'batch_limit':2},headers=H('operator')).json()
    if rr['report']['completed']:break
c.post(f'/sync-drills/{did2}/link',json={'online':False},headers=H('operator'))
w2({'site':'A','op':'delete','key':'x'})            # A clock 3, strictly newest tombstone
c.post(f'/sync-drills/{did2}/link',json={'online':True},headers=H('operator'))
for _ in range(10):
    rr=c.post(f'/sync-drills/{did2}/sync',json={'batch_limit':2},headers=H('operator')).json()
    if rr['report']['completed']:break
d2=c.get(f'/sync-drills/{did2}',headers=H('operator')).json()
check('strictly newer tombstone never resurrects',d2['invariants']['converged'] and 'x' not in d2['sites']['A']['kv'] and 'x' not in d2['sites']['B']['kv'],str(d2['sites']['A']['kv']))

# --- delete_wins policy on true concurrency (equal clocks) ---
r=c.post('/sync-drills',json={'name':'删除胜策略','policy':'delete_wins'},headers=H('operator'));did3=r.json()['id']
c.post(f'/sync-drills/{did3}/link',json={'online':False},headers=H('operator'))
c.post(f'/sync-drills/{did3}/writes',json={'site':'A','op':'delete','key':'c'},headers=H('operator'))
c.post(f'/sync-drills/{did3}/writes',json={'site':'B','op':'put','key':'c','value':'v'},headers=H('operator'))
c.post(f'/sync-drills/{did3}/link',json={'online':True},headers=H('operator'))
for _ in range(10):
    rr=c.post(f'/sync-drills/{did3}/sync',json={'batch_limit':5},headers=H('operator')).json()
    if rr['report']['completed']:break
d3=c.get(f'/sync-drills/{did3}',headers=H('operator')).json()
check('delete_wins keeps tombstone on tie',d3['invariants']['converged'] and 'c' not in d3['sites']['A']['kv'])

# --- validation and error codes ---
r=write('A','put','')
check('empty key 422',r.status_code==422)
r=c.get('/sync-drills/9999',headers=H('operator'))
check('missing drill 404',r.status_code==404)
r=c.post(f'/sync-drills/{did}/reset',headers=H('operator'))
check('reset clears state',r.status_code==200 and r.json()['backlog']==0 and r.json()['sites']['A']['kv']=={})
r=c.post('/sync-drills/9999/sync',json={'batch_limit':1},headers=H('operator'))
check('sync missing 404',r.status_code==404)

print()
if fails:print('FAILURES:',fails);sys.exit(1)
print('ALL SYNC API CHECKS PASSED')
