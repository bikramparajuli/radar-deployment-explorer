import numpy as np
import pytest
from shapely.geometry import Polygon, MultiPolygon, box, Point, mapping
from optimizer import solve_triangles, deployment_geometry, optimize


def test_known_optimum():
    polygon=box(0,0,10,10)
    r=solve_triangles(polygon,[[2,2],[3,2],[8,8]],[4,6,3],1.1,tolerance=0,max_seconds=3)
    assert r['score']==10
    assert r['score']<=10<=r['upper_bound']
    assert polygon.covers(Point(r['xy']))
    assert r['relative_gap']==0


def test_concavity_holes_disjoint():
    polygon=Polygon([(0,0),(9,0),(9,9),(6,9),(6,3),(3,3),(3,9),(0,9)],holes=[[(.5,.5),(2,.5),(2,2),(.5,2)]])
    combined=MultiPolygon([polygon,box(11,0,15,4)])
    r=solve_triangles(combined,[[4.5,7],[12,2],[13,2]],[50,4,6],1.1,max_seconds=3)
    assert combined.covers(Point(r['xy']))
    assert r['score']==10
    assert r['upper_bound']>=10


def test_early_exit_bound():
    rng=np.random.default_rng(3)
    xy=rng.uniform(0,10,(30,2)); weights=rng.uniform(1,10,30)
    r=solve_triangles(box(0,0,10,10),xy,weights,2,max_nodes=0)
    scores=[weights[np.linalg.norm(xy-p,axis=1)<=2].sum() for p in rng.uniform(0,10,(1000,2))]
    assert r['upper_bound']+1e-7>=max(scores)
    assert r['termination']=='iteration_limit'


def test_zero_and_invalid_weights():
    r=solve_triangles(box(0,0,10,10),np.empty((0,2)),[],2)
    assert r['score']==r['upper_bound']==0
    with pytest.raises(ValueError): solve_triangles(box(0,0,10,10),[[1,1]],[-1],2)


def test_geometry_validation():
    with pytest.raises(ValueError): deployment_geometry(None)
    with pytest.raises(ValueError): deployment_geometry({'features':[{'geometry':mapping(Point(-98,35))}]})
    with pytest.raises(ValueError): deployment_geometry({'features':[{'geometry':mapping(Polygon([(-99,34),(-97,36),(-99,36),(-97,34)]))}]})


def test_projected_end_to_end_outside_demand():
    forecast={'lon':np.array([-97.,-97.01,-97.02]),'lat':np.array([35.,35.01,35.02]),'values':np.array([20.,30.,10.]),'bounds':[-100,33,-95,38],'metadata':{}}
    polygon=box(-98,34.9,-97.6,35.2)
    geometry={'features':[{'geometry':mapping(polygon)}]}
    result=optimize(forecast,geometry,100,0,.01)
    assert result['score']==60
    assert result['covered_demand_percent']==100
    assert polygon.covers(Point(result['longitude'],result['latitude']))
    with pytest.raises(ValueError): optimize(forecast,geometry,100,60,.01)
