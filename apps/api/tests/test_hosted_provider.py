import httpx
import pytest
from app import models
from app.intelligence.schemas import SearchRequest


def test_hosted_json_mode_has_no_local_decoder_extensions(monkeypatch):
    captured={}
    def post(url, **kwargs):
        captured.update(kwargs['json'])
        return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':'{"query":"joinery suppliers"}'}}],'usage':{'total_tokens':7}})
    monkeypatch.setattr(httpx,'post',post)
    monkeypatch.setenv('AYVEN_LOCAL_LLM_BASE_URL','https://provider.example/v1')
    monkeypatch.setenv('AYVEN_PROVIDER_FORMAT','json_object')
    text,tokens,meta=models.complete_role('EMPLOYEE','Plan a search','Find suppliers',schema=SearchRequest)
    assert captured['response_format']=={'type':'json_object'}
    assert not {'guided_json','grammar'} & captured.keys()
    assert 'Return JSON matching this schema' in captured['messages'][0]['content']
    assert meta['constrained']['postcheck']=='accepted' and tokens==7


def test_live_mode_does_not_silently_return_fixtures(monkeypatch):
    monkeypatch.delenv('AYVEN_LOCAL_LLM_BASE_URL',raising=False)
    monkeypatch.setenv('AYVEN_LLM_STUB','0')
    with pytest.raises(RuntimeError,match='fixture fallback is disabled'):
        models.complete_role('EMPLOYEE','Do work','Find suppliers')
