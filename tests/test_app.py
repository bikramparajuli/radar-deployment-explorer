from app import server

def test_dash_and_health():
    client=server.test_client()
    assert client.get('/healthz').json=={'status':'ok'}
    assert client.get('/').status_code==200
    response=client.get('/_dash-layout')
    assert response.status_code==200
    assert 'Optimize radar location' in response.text
    assert client.get('/_dash-dependencies').status_code==200


def test_run_callback_and_export(monkeypatch):
    import json
    import numpy as np
    import app as module
    from shapely.geometry import box,mapping
    forecast={'lon':np.array([-97.,-97.01,-97.02]),'lat':np.array([35.,35.01,35.02]),'values':np.array([20.,30.,10.]),'bounds':[-100,33,-95,38],'metadata':{}}
    monkeypatch.setattr(module,'get_forecast',lambda key:forecast)
    geometry={'type':'FeatureCollection','features':[{'type':'Feature','geometry':mapping(box(-98,34.9,-97.6,35.2)),'properties':{}}]}
    callback=next(k for k in module.app.callback_map if 'result-data.data' in k)
    inputs=[('run','n_clicks',1),('draw','geojson',geometry),('forecast-key','data','key'),('radius','value',100),('threshold','value',0),('tolerance','value',.01)]
    outputs=[('solution','children'),('metrics','children'),('result-status','children'),('result-data','data'),('export','disabled')]
    response=server.test_client().post('/_dash-update-component',json={'output':callback,'outputs':[{'id':i,'property':p} for i,p in outputs], 'inputs':[{'id':i,'property':p,'value':v} for i,p,v in inputs],'state':[],'changedPropIds':['run.n_clicks']})
    assert response.status_code==200
    data=response.json['response']
    assert data['result-data']['data']['score']==60
    assert len(data['solution']['children'])==2
    assert data['export']['disabled'] is False
    exported=module.export_result(1,data['result-data']['data'])
    collection=json.loads(exported['content'])
    assert len(collection['features'])==3
    assert collection['features'][0]['geometry']['type']=='Point'
