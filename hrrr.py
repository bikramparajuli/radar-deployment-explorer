"""Fetch only HRRR composite reflectivity from NOAA NOMADS; no API key.
Native grid coordinates are used for demand. Regridding is display-only.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import requests
from diskcache import Cache, Lock
from eccodes import (codes_grib_new_from_file, codes_get, codes_get_array, codes_release)
from PIL import Image
from pyproj import Transformer
from scipy.spatial import cKDTree

ENDPOINT = 'https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl'
CACHE = Cache(os.environ.get('RADAR_CACHE_DIR', '/tmp/radar-hrrr-cache'), size_limit=300*1024**2)
TTL = 6*3600


def validate_bounds(bounds):
    w, s, e, n = map(float, bounds)
    if not all(np.isfinite([w,s,e,n])) or not (-125 <= w < e <= -66 and 24 <= s < n <= 50):
        raise ValueError('Keep the map extent within the contiguous United States (24–50°N, 125–66°W).')
    if e-w > 12 or n-s > 10 or e-w < .1 or n-s < .1:
        raise ValueError('Zoom to a region between 0.1° and 12° wide, and at most 10° tall.')
    return [round(v, 3) for v in (w,s,e,n)]


def _get_text(params=None):
    r = requests.get(ENDPOINT, params=params, timeout=(8,25))
    r.raise_for_status()
    return r.text


def recent_runs(lead):
    lead = int(lead)
    if not 0 <= lead <= 18:
        raise ValueError('Choose a forecast lead from 0 to 18 hours.')
    key = f'runs:{lead}'
    cached = CACHE.get(key)
    if cached:
        return cached
    dates = sorted(set(re.findall(r'hrrr\.(\d{8})', _get_text())), reverse=True)
    runs = []
    for date in dates[:2]:
        page = _get_text({'dir': f'/hrrr.{date}/conus'})
        cycles = sorted(set(re.findall(rf'hrrr\.t(\d{{2}})z\.wrfsfcf{lead:02d}\.grib2', page)), reverse=True)
        runs.extend((date, int(cycle)) for cycle in cycles)
        if runs:
            break
    if not runs:
        raise ValueError('NOAA has no available run for this lead right now. Try again shortly.')
    CACHE.set(key, runs[:3], expire=180)
    return runs[:3]


def decode_grib(path, bounds):
    with open(path, 'rb') as f:
        gid = codes_grib_new_from_file(f)
        if gid is None:
            raise ValueError('NOAA returned an empty GRIB file.')
        try:
            short_name = str(codes_get(gid, 'shortName'))
            if short_name != 'refc':
                raise ValueError(f'Expected composite reflectivity (refc); received {short_name}.')
            values = codes_get_array(gid, 'values').astype(np.float32)
            lon = codes_get_array(gid, 'longitudes')
            lon = (lon+180)%360-180
            lat = codes_get_array(gid, 'latitudes')
            missing = float(codes_get(gid, 'missingValue'))
            values[values == missing] = np.nan
            metadata = {k: str(codes_get(gid, k)) for k in
                        ('dataDate','dataTime','validityDate','validityTime','stepRange','name','units')}
        finally:
            codes_release(gid)
    w, s, e, n = bounds
    keep = (lon>=w)&(lon<=e)&(lat>=s)&(lat<=n)
    if keep.sum() < 4:
        raise ValueError('Too few HRRR cells in this extent. Zoom out slightly.')
    if keep.sum() > 200000:
        raise ValueError('Forecast area is too large. Zoom in and load again.')
    return {'values':values[keep], 'lon':lon[keep], 'lat':lat[keep],
            'bounds':bounds, 'metadata':metadata}


def fetch_forecast(bounds, lead=3):
    bounds = validate_bounds(bounds)
    if float(lead) != int(lead):
        raise ValueError('Forecast lead must be an integer number of hours.')
    last_error = None
    for date, cycle in recent_runs(lead)[:1]:
        params = {'dir':f'/hrrr.{date}/conus', 'file':f'hrrr.t{cycle:02d}z.wrfsfcf{int(lead):02d}.grib2',
                  'var_REFC':'on', 'lev_entire_atmosphere':'on', 'subregion':'',
                  'leftlon':bounds[0], 'rightlon':bounds[2], 'bottomlat':bounds[1], 'toplat':bounds[3]}
        key = hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()
        with Lock(CACHE, f'lock:{key}', expire=100):
            cached = CACHE.get(key)
            if cached is not None:
                return key, cached
            try:
                started = time.monotonic()
                with requests.get(ENDPOINT, params=params, timeout=(8,40), stream=True) as r:
                    r.raise_for_status()
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory)/'reflectivity.grib2'
                        size = 0
                        with open(path,'wb') as out:
                            for chunk in r.iter_content(128*1024):
                                size += len(chunk)
                                if size > 15*1024**2 or time.monotonic()-started > 60:
                                    raise ValueError('Forecast download exceeded the regional size or time limit.')
                                out.write(chunk)
                        with open(path,'rb') as f:
                            if f.read(4) != b'GRIB':
                                raise ValueError('NOAA returned a page instead of GRIB data; the run may still be arriving.')
                        forecast = decode_grib(path, bounds)
                forecast['metadata'].update(source='NOAA NOMADS / HRRR CONUS',
                    source_url=r.url, retrieved_utc=datetime.now(timezone.utc).isoformat())
                forecast['image'] = overlay_image(forecast)
                CACHE.set(key, forecast, expire=TTL)
                return key, forecast
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                # Do not silently switch valid times after a failed download.
                break
    raise ValueError(f'Unable to load HRRR: {last_error}. Please retry or select a shorter forecast lead.')


def get_forecast(key):
    if not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{64}',key):
        raise ValueError('Load an HRRR forecast first.')
    forecast = CACHE.get(key)
    if forecast is None:
        raise ValueError('This forecast expired from the server cache. Load HRRR again.')
    return forecast


def overlay_image(forecast, width=700):
    """Nearest native cell, north-up Web Mercator PNG for Leaflet ImageOverlay."""
    transformer = Transformer.from_crs(4326,3857,always_xy=True)
    x,y = transformer.transform(forecast['lon'],forecast['lat'])
    w,s,e,n = forecast['bounds']
    left,bottom = transformer.transform(w,s)
    right,top = transformer.transform(e,n)
    height = int(np.clip(width*(top-bottom)/(right-left),100,900))
    gx,gy = np.meshgrid(np.linspace(left,right,width),np.linspace(top,bottom,height))
    tree=cKDTree(np.column_stack((x,y)))
    distance,index=tree.query(np.column_stack((gx.ravel(),gy.ravel())))
    values=forecast['values'][index]
    bins=np.array([0,5,10,15,20,25,30,35,40,45,50,55,60,65,70])
    colors=np.array(['#80c4e9','#4b9cd3','#3060b0','#40c78e','#139f50','#08752f',
                     '#e4e441','#f5bd34','#ef8d24','#e44c32','#c82735','#a01b55',
                     '#bc54ad','#df95d8','#fafafa'])
    rgb=np.array([[int(c[i:i+2],16) for i in (1,3,5)] for c in colors],dtype=np.uint8)
    rgba=np.zeros((len(values),4),dtype=np.uint8)
    finite=np.isfinite(values)&(values>=0)&(distance<9000)
    rgba[finite,:3]=rgb[np.clip(np.digitize(values[finite],bins)-1,0,len(rgb)-1)]
    rgba[finite,3]=215
    output=io.BytesIO()
    Image.fromarray(rgba.reshape(height,width,4)).save(output,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()
