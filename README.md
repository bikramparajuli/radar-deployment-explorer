# Radar Deployment Explorer

A Python web application that downloads NOAA HRRR composite reflectivity,
shows it on an interactive map, lets visitors draw deployment polygons, and
runs a single-radar spatial optimization inside those polygons. No input
datasets, NOAA API key, ArcGIS license, or notebook session are required.

## Run on your computer

Use Python 3.12 (64-bit). Extract this folder and open a terminal in it.

```bash
python -m venv .venv
```

Activate the environment:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- Ubuntu, WSL, macOS: `source .venv/bin/activate`

Then:

```bash
python -m pip install -r requirements.txt
python app.py
```

Open http://localhost:8050 in your browser. Internet access is required for
HRRR downloads, map tiles, and the optional web fonts. Modern ecCodes Python
wheels supply the decoder library on supported platforms. Use the Docker
route below if your operating system cannot install a compatible wheel.

## How to use it

1. Pan and zoom to a regional study area in the contiguous United States.
2. Choose a forecast lead from 0–18 hours and click **Load HRRR for this map**.
   The app discovers the latest run that lists that lead. Initialization and
   valid time appear beneath the button. A 0-hour lead is an analysis.
3. Draw a polygon or rectangle with the map toolbar. Multiple areas are
   supported and are combined. Edit/delete drawings with the same toolbar.
4. Choose radar range, reflectivity threshold, and relative bound tolerance.
5. Click **Optimize radar location**. Inspect the radar marker, coverage,
   covered-demand percentage, bound gap, coordinates, and elapsed solver time.
6. Download a GeoJSON containing the location, coverage, deployment polygon,
   forecast provenance, objective definition, parameters, and solver status.

The drawn polygon controls where the radar may be placed. Demand outside
that polygon can still be covered. The loaded map extent, outlined with a
dashed rectangle, defines the available demand region. Load a wider region
if the coverage reaches its edge. Changing the forecast, polygon, or solver
parameters clears the old result. Panning alone does not change loaded data.

## Publish publicly on Render

This is a Python application, so it cannot execute on GitHub Pages alone.
The included Dockerfile runs the Python backend with Gunicorn. Render can
build this Docker image directly from your GitHub repository.

### What you need to do

1. Create a GitHub repository, for example `radar-deployment-explorer`.
2. Upload the **contents** of this folder to the repository root. `app.py`,
   `Dockerfile`, `requirements.txt`, and `render.yaml` should appear directly
   at the root, with `assets/` and `tests/` as directories. Do not upload the
   ZIP itself or your local virtual environment.
3. Create or sign into your own Render account and connect that repository.
4. Choose **New → Web Service**, select the repository, and set **Language:
   Docker**. Leave the Dockerfile path as `./Dockerfile` and the Docker
   command override blank so it uses the provided command.
5. Select a compute plan. The free plan is a starting point for a portfolio
   demonstration, subject to Render's current limits; it is not a guarantee
   of adequate memory or reliability. For regular public use, select an
   always-on paid instance after checking resource usage. No purchase has
   been made or service provisioned for you.
6. Set the health-check path to `/healthz` and deploy. Render supplies the
   public HTTPS URL. A custom domain is optional.
7. Visit that URL, load HRRR, draw a polygon, run optimization, and download
   the GeoJSON. Check Render logs and memory use before sharing broadly.

Alternatively, use **New → Blueprint** with this repository and the supplied
`render.yaml`. That file explicitly selects the free plan. Review the plan
and any billing prompts in your account before deploying.

No application secrets, database, persistent disk, or NOAA account are
required. The forecast cache is disposable. It repopulates after restart.
Anyone with the resulting public URL can run the app.

Render's free service sleeps after 15 minutes without traffic and may take
about a minute to wake up. Its filesystem is ephemeral. Usage limits also
apply. The app's regional download limits and cache reduce unnecessary
requests but do not eliminate hosting or bandwidth limits.

Official references:
- Docker hosting: https://render.com/docs/docker
- Blueprint configuration: https://render.com/docs/blueprint-spec
- Free service limitations: https://render.com/docs/free

### Test the deployment container locally (optional)

```bash
docker build -t radar-deployment-explorer .
docker run --rm -p 8050:8050 radar-deployment-explorer
```

No Docker installation is needed on your computer if Render builds it.
`PORT` is supplied by the host; it defaults to 8050 for local use.

## Model and scientific scope

The demand weight at each native HRRR cell center is its positive composite
reflectivity in dBZ, provided it meets the selected threshold. The score is:

    sum of demand weights whose cell centers lie within radar range

This retains the notebook's positive-reflectivity coverage ranking for fixed
range. It deliberately omits the notebook's constant radius/sample-count
normalization. The numerical score therefore is not interchangeable with
its area-normalized output. A sum of dBZ is a user-defined coverage index,
not physical radar power or an integral of linear reflectivity Z.

Distances are planar meters in a local WGS84 azimuthal equidistant projection
centered on the deployment polygon. Projection distortion remains possible
away from that center. The guarantee applies to this projected, discrete
cell-center demand model, not to continuous storm coverage or geodesic
accuracy everywhere. HRRR cells are used at their native resolution;
interpolation of the display does not add analytical resolution.

The solver retains triangle branch-and-bound and four-way midpoint
subdivision from the notebook. Important corrections include:

- Constrained triangulation respects concavities, holes, and separate areas.
- Each upper bound remains paired with its own triangle in a priority queue.
- Search does not stop merely because one triangle remains.
- Nonnegative weights permit an upper bound from a centroid-centered disk
  of radius R+d, where d is the maximum distance to a triangle vertex.
- The bound uses the same weights and normalization as the feasible score.
- Time/iteration limits return the best feasible solution and remaining gap;
  the app does not label an interrupted run as tolerance-certified.
- Native GRIB coordinates stay paired with their values. The map PNG is
  resampled in Web Mercator only for display, with north at the top.

Covered-demand percentage is the score divided by the total positive demand
above the threshold in the **loaded extent**. It is not percentage of land
area. Locations on the polygon boundary are allowed.

The entire drawn polygon is feasible. This version does not automatically
exclude water, buildings, trees, inaccessible roads, or private land. It does
not model beam blockage, attenuation, or vertical sampling. These are explicit
limits of this prototype, not claims of operational suitability.

## Data and performance

- NOAA NOMADS: https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl
- Requests only `REFC` at `entire atmosphere` from `wrfsfc` GRIB2 files.
- Discovers dates and cycles from NOAA's current listing, rather than assuming
  a hard-coded date is still available.
- Handles HTTP failures, missing/invalid GRIB, empty demand, invalid polygons,
  out-of-extent polygons, and cache expiration with visible messages.
- Uses finite timeouts, bounded regional downloads and a six-hour cache.
- Uses two work slots per server to bound concurrent expensive requests.
- Requests are limited to a 12° × 10° forecast extent, 200,000 cells, eight
  polygons (each at most 1,000 vertices), and a 5–150 km radar range.
- Optimization has a 20-second / 100,000-subdivision cap.
- No login, saved user projects, or background job queue is included.

For larger traffic, add per-client rate limiting and monitor memory. Multiple
server replicas require shared caching/limits if you want global consistency.
The included configuration runs one instance with a threaded Gunicorn worker.

## Files and extension to dual-Doppler

| File | Purpose |
| --- | --- |
| `app.py` | Dash interface, callbacks, result export, health check |
| `hrrr.py` | NOAA discovery, downloading, GRIB decoding, caching, map overlay |
| `optimizer.py` | Geometry validation and single-radar optimizer |
| `assets/style.css` | Responsive map and control-panel styling |
| `Dockerfile` | Python production server environment |
| `render.yaml` | Optional Render Blueprint |
| `tests/` | Geometry, solver, overlay, server and export checks |

Dual-Doppler should be added as a separate solver that jointly optimizes two
locations, with its own crossing-angle, separation, and overlap objective.
The existing forecast loading, demand preparation, drawing controls and map
can be reused. Running the single-radar solver twice is not a substitute.

## Verification in this delivery

Run the automated checks with:

```bash
python -m pip install pytest
python -m pytest -q
```

Tests cover a known optimum, holes/concavity/disjoint polygons, zero demand,
invalid inputs, early-stop upper bounds, demand outside the deployment
polygon, north-up image orientation, Dash HTTP endpoints, the optimization
callback and GeoJSON export.

A live NOAA test downloaded the 2026-11-06 00 UTC HRRR run, +3 hours, over
100–96°W / 34–37°N. It decoded 13,432 cells in approximately four seconds.
For a rectangular deployment area of 99.5–96.5°W / 34.5–36.5°N and a 75 km
range, the solver reached the 1% tolerance in approximately 0.09 seconds
(91 subdivisions). These timings are from the development environment and
are not hosting guarantees. The live forecast file is not bundled.

The browser preview could not connect to the local development server, so
visual browser interaction has not been verified. Docker was unavailable in
the development environment, so the container build and Render deployment
have not been executed. Complete the public-URL smoke test described above.
