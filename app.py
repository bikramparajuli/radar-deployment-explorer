"""Public-ready Dash entry point. Run: python app.py"""
import json
import logging
import os
from contextlib import contextmanager
import dash_leaflet as dl
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
from hrrr import CACHE, fetch_forecast, get_forecast
from optimizer import optimize

logging.basicConfig(level=logging.INFO)
app = Dash(__name__, title='Radar | Deployment Explorer', update_title='Working…')
server = app.server
server.config['MAX_CONTENT_LENGTH'] = 2*1024*1024

@server.get('/healthz')
def health():
    return {'status':'ok'}

@contextmanager
def work_slot():
    # Two simultaneous expensive requests per server; excess gets a retry message.
    # Shared on disk across Gunicorn workers; no globally shared user polygons.
    slot = next((f'busy:{i}' for i in range(2) if CACHE.add(f'busy:{i}',True,expire=240)),None)
    if slot is None:
        raise ValueError('The server is handling other requests. Please try again in a moment.')
    try:
        yield
    finally:
        CACHE.delete(slot)


def field(label, control, hint=None):
    return html.Div([html.Label(label, htmlFor=control.id), control, html.Small(hint) if hint else None], className='field')


def layout():
    return html.Div([
        dcc.Store(id='forecast-key'), dcc.Store(id='result-data'), dcc.Download(id='download'),
        html.Header([
            html.Div([html.Span('◉',className='brand-icon'), html.Div([html.H1('Radar Deployment Explorer'),
                html.P('HRRR forecasts · Single-radar optimization')])],className='brand'),
            html.Span('RESEARCH PROTOTYPE',className='badge')]),
        html.Main([
            html.Aside([
                html.Div([html.Span('01',className='step'),html.H2('Load the forecast')],className='section-title'),
                html.P('Pan and zoom to your study region. Load the latest available HRRR run for that map extent.',className='intro'),
                field('Forecast lead', dcc.Dropdown(id='lead',options=[{'label':f'+{i} hours' if i else 'Analysis (0 hours)','value':i} for i in range(19)], value=3,clearable=False)),
                html.Button('Load HRRR for this map',id='load',className='primary full'),
                dcc.Loading(html.Div('No forecast loaded yet.',id='forecast-status',className='status',role='status'),type='circle'),
                html.Hr(),
                html.Div([html.Span('02',className='step'),html.H2('Draw a deployment area')],className='section-title'),
                html.P('Use the polygon or rectangle tool on the map. The radar must be inside your drawing. You can edit or delete it.',className='intro'),
                html.Div('The polygon constrains the radar location. Reflectivity outside it can still contribute to coverage.',className='note'),
                html.Hr(),
                html.Div([html.Span('03',className='step'),html.H2('Find a radar location')],className='section-title'),
                field('Radar range (km)',dcc.Input(id='radius',type='number',min=5,max=150,step=5,value=75)),
                field('Minimum demand reflectivity (dBZ)',dcc.Input(id='threshold',type='number',min=0,max=65,step=5,value=0), 'Positive reflectivity above this threshold contributes to the score.'),
                field('Optimization tolerance',dcc.Dropdown(id='tolerance',options=[{'label':'0.1%','value':.001},{'label':'1%','value':.01},{'label':'5%','value':.05}],value=.01,clearable=False)),
                html.Button('Optimize radar location',id='run',className='primary full'),
                dcc.Loading(html.Div(id='result-status',role='status',className='status'),type='circle'),
                html.Div(id='metrics'),
                html.Button('Download result · GeoJSON',id='export',className='secondary full',disabled=True),
                html.Details([html.Summary('About this model'),html.P('Maximizes the sum of positive HRRR composite reflectivity at native grid cell centers within radar range. This is a weighted coverage index, not physical returned power. Only the loaded forecast extent contributes.'),
                    html.P('Triangle branch-and-bound searches the drawn area in a local projected coordinate system. The displayed gap applies to this discrete-demand model. No terrain, road access, land ownership, beam blockage or attenuation constraints are included.'),
                    html.P('Single-radar mode. Dual-Doppler optimization can be added as a separate solver.')]),
            ],className='sidebar'),
            html.Section([
                html.Div([html.Span('DEPLOYMENT MAP'),html.Span('Draw → optimize → inspect')],className='map-heading'),
                dl.Map([
                    dl.TileLayer(url='https://tile.openstreetmap.org/{z}/{x}/{y}.png',attribution='© OpenStreetMap contributors'),
                    dl.LayerGroup(id='weather'),
                    dl.FeatureGroup([dl.EditControl(id='draw',position='topleft',
                        draw={'polyline':False,'circle':False,'circlemarker':False,'marker':False,
                              'polygon':{'allowIntersection':False,'shapeOptions':{'color':'#1c514b'}},
                              'rectangle':{'shapeOptions':{'color':'#1c514b'}}},
                        edit={'edit':True,'remove':True})]),
                    dl.LayerGroup(id='solution'),dl.ScaleControl(position='bottomleft',imperial=False),
                ],id='map',center=[35.5,-98.3],zoom=7,minZoom=5,maxZoom=14,style={'height':'100%','width':'100%'}),
                html.Div([html.Span('Composite reflectivity'),html.Div(className='colorbar'),
                    html.Div([html.Span('0'),html.Span('20'),html.Span('40'),html.Span('60+ dBZ')],className='legend-ticks')],className='legend'),
                html.Div('Forecast: NOAA / NCEP HRRR · Basemap: OpenStreetMap',className='map-footer'),
            ],className='map-panel'),
        ]),
    ])
app.layout = layout


def forecast_label(metadata):
    def stamp(date, time):
        date,time=str(date),str(time).zfill(4)
        return f'{date[:4]}-{date[4:6]}-{date[6:8]} {time[:2]}:{time[2:]} UTC'
    return [html.Strong('HRRR composite reflectivity'),
        html.Div('Initialized '+stamp(metadata['dataDate'],metadata['dataTime'])),
        html.Div('Valid '+stamp(metadata['validityDate'],metadata['validityTime']))]

@app.callback(Output('forecast-key','data'),Output('weather','children'),Output('forecast-status','children'),
              Input('load','n_clicks'),State('map','bounds'),State('lead','value'),prevent_initial_call=True,
              running=[(Output('load','disabled'),True,False)])
def load_forecast(clicks, bounds, lead):
    try:
        bounds = bounds or [[33.4,-103.2],[37.4,-94.2]]
        (south,west),(north,east)=bounds
        with work_slot():
            key, forecast = fetch_forecast([west,south,east,north],lead)
        w,s,e,n=forecast['bounds']
        layer=dl.ImageOverlay(url=forecast['image'],bounds=[[s,w],[n,e]],opacity=.78,interactive=False)
        extent=dl.Rectangle(bounds=[[s,w],[n,e]],color='#60706b',weight=1,dashArray='5 5',fill=False,interactive=False)
        return key,[layer,extent],forecast_label(forecast['metadata'])
    except (ValueError,TypeError) as exc:
        return None,[],html.Span(str(exc),className='error')
    except Exception:
        logging.exception('Forecast download failed')
        return None,[],html.Span('The forecast service is temporarily unavailable. Please try again.',className='error')

@app.callback(Output('solution','children'),Output('metrics','children'),Output('result-status','children'),
              Output('result-data','data'),Output('export','disabled'),
              Input('run','n_clicks'),Input('draw','geojson'),Input('forecast-key','data'),
              Input('radius','value'),Input('threshold','value'),Input('tolerance','value'),
              prevent_initial_call=True,running=[(Output('run','disabled'),True,False)])
def run_optimizer(clicks, geometry, key, radius, threshold, tolerance):
    if ctx.triggered_id != 'run':
        return [],[], '',None,True
    try:
        if any(value is None for value in (radius,threshold,tolerance)):
            raise ValueError('Fill in the radar range, threshold and tolerance.')
        with work_slot():
            result=optimize(get_forecast(key),geometry,radius,threshold,tolerance)
        point=[result['latitude'],result['longitude']]
        layers=[dl.GeoJSON(data={'type':'Feature','geometry':result['coverage'],'properties':{}},
                          options={'style':{'color':'#bd5d32','weight':2,'fillOpacity':.08}}),
                dl.CircleMarker(center=point,radius=8,color='#ffffff',weight=2,fillColor='#bd5d32',fillOpacity=1,
                    children=[dl.Tooltip('Optimized radar location',permanent=True,direction='top'),
                              dl.Popup(f'{point[0]:.5f}, {point[1]:.5f}')])]
        gap=result['relative_gap']
        status='Requested tolerance reached.' if result['termination']=='tolerance' else 'Time or iteration limit reached; best location found is shown.'
        if result['edge_warning']:
            status+=' Coverage reaches beyond the loaded forecast. Load a wider region to include that demand.'
        metrics=html.Div([
            html.Div([html.Small('COVERED DEMAND'),html.Strong(f"{result['covered_demand_percent']:.1f}%")],className='metric'),
            html.Div([html.Small('BOUND GAP'),html.Strong(f'{gap*100:.2f}%' if gap is not None else 'Unbounded')],className='metric'),
            html.Div(f"{point[0]:.5f}, {point[1]:.5f}",className='coordinates'),
            html.Small(f"Score {result['score']:,.1f} · {result['seconds']:.2f} s · {result['demand_cells']:,} demand cells")
        ],className='metrics')
        return layers,metrics,status,result,False
    except (ValueError,TypeError) as exc:
        return [],[],html.Span(str(exc),className='error'),None,True
    except Exception:
        logging.exception('Optimization failed')
        return [],[],html.Span('Optimization failed. Please check the polygon and try again.',className='error'),None,True

@app.callback(Output('download','data'),Input('export','n_clicks'),State('result-data','data'),prevent_initial_call=True)
def export_result(clicks,result):
    if not result:
        raise PreventUpdate
    properties={k:v for k,v in result.items() if k not in ('coverage','deployment_polygon')}
    collection={'type':'FeatureCollection','features':[
        {'type':'Feature','geometry':{'type':'Point','coordinates':[result['longitude'],result['latitude']]},'properties':dict(properties,layer='radar')},
        {'type':'Feature','geometry':result['coverage'],'properties':{'layer':'coverage','radius_km':result['radius_km']}},
        {'type':'Feature','geometry':result['deployment_polygon'],'properties':{'layer':'deployment_area'}}]}
    return dcc.send_string(json.dumps(collection,indent=2,allow_nan=False),'radar-deployment.geojson')

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',8050)),debug=False)
