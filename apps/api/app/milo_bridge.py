"""Read contract used by the existing Milo client; IDs remain engine project IDs."""
import os
from fastapi import APIRouter, HTTPException
from .db import connect

router = APIRouter(prefix='/api/v1')
STATUS = {'complete':'COMPLETED','waiting':'BLOCKED','running':'RUNNING','rejected':'FAILED'}


def rows(sql, args=()):
    conn=connect()
    result=[dict(row) for row in conn.execute(sql,args).fetchall()]
    conn.close()
    return result


def project_view(row):
    return {'id':row['id'],'name':row['title'],'description':row['objective'],
            'status':STATUS.get(row['status'],row['status'].upper()),
            'updatedAt':row['created_at'],'result':row['result'],
            'blockers':[{'title':'Needs your input or approval'}] if row['status']=='waiting' else []}


def mission_view(row):
    return {'id':row['id'],'projectId':row['id'],'purpose':row['objective'],
            'status':STATUS.get(row['status'],row['status'].upper()),
            'updatedAt':row['created_at'],'result':row['result']}


@router.get('/products')
def products():
    # This engine publishes its own service, never invents products from legacy OS.
    return {'items':[{'id':'ayven-engine','displayName':'Ayven workforce engine',
                     'slug':'ayven-engine','description':'Durable research, review and approvals',
                     'lifecycleStatus':'ACTIVE','launchMode':'web','currentUrl':(os.environ.get('AYVEN_PUBLIC_URL','').rstrip('/')+'/campus') if os.environ.get('AYVEN_PUBLIC_URL') else None}]}


@router.get('/products/{product_id}')
def product(product_id: str):
    if product_id != 'ayven-engine':
        raise HTTPException(404)
    return products()['items'][0]


@router.get('/projects')
def projects():
    return {'items':[project_view(row) for row in rows('SELECT * FROM projects ORDER BY created_at DESC LIMIT 100')]}


@router.get('/projects/{project_id}')
def project(project_id: str):
    found=rows('SELECT * FROM projects WHERE id=?',(project_id,))
    if not found: raise HTTPException(404)
    return project_view(found[0])


@router.get('/missions')
def missions():
    return {'items':[mission_view(row) for row in rows('SELECT * FROM projects ORDER BY created_at DESC LIMIT 100')]}


@router.get('/missions/{mission_id}')
def mission(mission_id: str):
    found=rows('SELECT * FROM projects WHERE id=?',(mission_id,))
    if not found: raise HTTPException(404)
    return mission_view(found[0])
