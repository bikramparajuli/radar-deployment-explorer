import base64
import io
import numpy as np
import pytest
from PIL import Image
from hrrr import overlay_image,validate_bounds


def test_overlay_orientation():
    f={'lon':np.array([-98,-97.99,-98,-97.99]),'lat':np.array([35.01,35.01,35,35]),'values':np.array([50.,50.,5.,5.]),'bounds':[-98,35,-97.99,35.01]}
    url=overlay_image(f,width=100)
    image=np.array(Image.open(io.BytesIO(base64.b64decode(url.split(',')[1]))))
    assert image[0,50,:3].tolist()==[200,39,53]
    assert image[-1,50,:3].tolist()==[75,156,211]


def test_bounds_validation():
    with pytest.raises(ValueError): validate_bounds([-180,-90,180,90])
    with pytest.raises(ValueError): validate_bounds([-120,30,-90,40])
    with pytest.raises(ValueError): validate_bounds([-98,35,-98,36])
