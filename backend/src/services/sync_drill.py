from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from ..models.sync_drill import SyncDrill
from . import sync_engine as eng

def _save(x):
    # The drill state is a nested JSON column; in-place engine mutations need an
    # explicit dirty flag or SQLAlchemy would not issue the UPDATE.
    flag_modified(x,'state')

def _view(x):
    st=x.state;v=eng.verify(st)
    sites={}
    for sid,site in st['sites'].items():
        tombstones=sorted({o['key'] for o in site['log'] if o['deleted']})
        sites[sid]={'online':site['online'],'clock':site['clock'],'kv':dict(sorted(site['kv'].items())),
                    'op_count':len(site['log']),'tombstones':tombstones,'checksum':v[f'checksum_{sid.lower()}']}
    return {'id':x.id,'name':x.name,'description':x.description,'policy':x.policy,'created_at':str(x.created_at),
            'online':all(s['online'] for s in st['sites'].values()),'sites':sites,'backlog':v['backlog'],
            'converged':v['converged'],'invariants':v,'conflicts':st['conflicts'][-30:][::-1],
            'events':st['events'][-60:][::-1],'state':st}

def listing(db):
    rows=db.scalars(select(SyncDrill).order_by(SyncDrill.id.desc())).all()
    return [{k:z for k,z in _view(x).items() if k not in ('state','events')} | {'event_count':len(x.state['events'])} for x in rows]

def detail(db,ident):
    x=db.get(SyncDrill,ident)
    if not x:raise HTTPException(404,'同步演练不存在')
    return _view(x)

def _get(db,ident):
    x=db.get(SyncDrill,ident)
    if not x:raise HTTPException(404,'同步演练不存在')
    return x

def create(db,data):
    if db.scalar(select(SyncDrill).where(SyncDrill.name==data.name)):raise HTTPException(409,'演练名称已存在')
    x=SyncDrill(name=data.name,description=data.description,policy=data.policy,state=eng.new_state(data.policy))
    db.add(x);db.commit();db.refresh(x);return _view(x)

def reset(db,ident):
    x=_get(db,ident);x.state=eng.new_state(x.policy);db.commit();return _view(x)

def write(db,ident,data):
    x=_get(db,ident)
    try:eng.write_local(x.state,data.site,data.op,data.key,data.value)
    except ValueError as e:raise HTTPException(422,str(e))
    _save(x);db.commit();return _view(x)

def link(db,ident,data):
    x=_get(db,ident);eng.set_link(x.state,data.online);_save(x);db.commit();return _view(x)

def sync(db,ident,data):
    x=_get(db,ident)
    try:report=eng.sync_batch(x.state,data.batch_limit)
    except ValueError as e:raise HTTPException(422,str(e))
    _save(x);db.commit();return {'report':report,'drill':_view(x)}
