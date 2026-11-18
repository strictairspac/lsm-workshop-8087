from fastapi import APIRouter,Depends
from ..config.database import get_db
from ..schemas.sync_drill import DrillInput,WriteInput,LinkInput,SyncInput
from ..services.auth import current_user,writer
from ..services import sync_drill as svc
router=APIRouter(prefix='/sync-drills')
@router.get('')
def get(db=Depends(get_db),user=Depends(current_user)):return svc.listing(db)
@router.post('',status_code=201)
def add(data:DrillInput,db=Depends(get_db),user=Depends(writer)):return svc.create(db,data)
@router.get('/{ident}')
def one(ident:int,db=Depends(get_db),user=Depends(current_user)):return svc.detail(db,ident)
@router.post('/{ident}/reset')
def reset(ident:int,db=Depends(get_db),user=Depends(writer)):return svc.reset(db,ident)
@router.post('/{ident}/writes')
def write(ident:int,data:WriteInput,db=Depends(get_db),user=Depends(writer)):return svc.write(db,ident,data)
@router.post('/{ident}/link')
def link(ident:int,data:LinkInput,db=Depends(get_db),user=Depends(writer)):return svc.link(db,ident,data)
@router.post('/{ident}/sync')
def sync(ident:int,data:SyncInput,db=Depends(get_db),user=Depends(writer)):return svc.sync(db,ident,data)
