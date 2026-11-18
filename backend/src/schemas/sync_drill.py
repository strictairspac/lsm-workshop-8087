from typing import Literal
from pydantic import BaseModel,Field
class DrillInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    description:str=Field(default='',max_length=2000)
    policy:Literal['lww','delete_wins','put_wins']='lww'
class WriteInput(BaseModel):
    site:Literal['A','B']
    op:Literal['put','delete']
    key:str=Field(min_length=1,max_length=64)
    value:str|None=Field(default=None,max_length=200)
class LinkInput(BaseModel):
    online:bool
class SyncInput(BaseModel):
    batch_limit:int=Field(default=5,ge=1,le=200)
