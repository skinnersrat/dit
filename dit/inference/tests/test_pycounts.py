"""
Tests for dit.inference.pycounts
"""

from __future__ import division

from nose.plugins.attrib import attr
from nose.tools import assert_true

from dit import Distribution

@attr('cython')
def test_dfd():
    """
    Test distribution_from_data.
    """
    from dit.inference import distribution_from_data
    data = [0,0,0,1,1,1]
    d1 = Distribution([(0,), (1,)], [1/2, 1/2])
    d2 = Distribution([(0,0), (0,1), (1,1)], [2/5, 1/5, 2/5])
    d1_ = distribution_from_data(data, 1)
    d2_ = distribution_from_data(data, 2)
    assert_true(d1.is_approx_equal(d1_))
    assert_true(d2.is_approx_equal(d2_))
