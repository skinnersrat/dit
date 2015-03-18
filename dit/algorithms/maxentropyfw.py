"""
Another way to do maxent without using the convex solver from CVXOPT.

This uses the Frank-Wolfe algorithm:

    http://en.wikipedia.org/wiki/Frank%E2%80%93Wolfe_algorithm

"""
from __future__ import print_function

import itertools
import numpy as np

import dit
from dit.abstractdist import get_abstract_dist

from .optutil import as_full_rank, CVXOPT_Template
from .maxentropy import marginal_constraints

__all__ = [
    'marginal_maxent_dists',
]


class Bunch:
    def __init__(self, **kwds):
        self.__dict__.update(kwds)


def prepare_dist(dist):
    if not isinstance(dist._sample_space, dit.samplespace.CartesianProduct):
        dist = dit.expanded_samplespace(dist, union=True)

    if not dist.is_dense():
        if len(dist._sample_space) > 1e4:
            import warnings
            msg = "Sample space has more than 10k elements."
            msg += " This could be slow."
            warnings.warn(msg)
        dist.make_dense()

    return dist


def op_runner(objective, constraints, **kwargs):
    from cvxopt.solvers import options
    from cvxopt.modeling import variable, op

    old_options = options.copy()

    opt = op(objective, constraints)

    try:
        options.clear()
        options.update(kwargs)
        with np.errstate(divide='ignore', invalid='ignore'):
            opt.solve()
    except:
        raise
    finally:
        options.clear()
        options.update(old_options)

    return opt


def isolate_zeros(dist, k):
    """
    Determines if there are any elements of the optimization vector that must
    be zero.

    If p(marginal) = 0, then every component of the joint that contributes to
    that marginal probability must be exactly zero for all feasible solutions.

    """
    assert dist.is_dense()
    assert dist.get_base() == 'linear'

    d = get_abstract_dist(dist)
    n_variables = d.n_variables
    n_elements = d.n_elements

    rvs = range(n_variables)
    zero_elements = np.zeros(n_elements, dtype=int)
    cache = {}
    pmf = dist.pmf
    for subrvs in itertools.combinations(rvs, k):
        marray = d.parameter_array(subrvs, cache=cache)
        for idx in marray:
            # Convert the sparse nonzero elements to a dense boolean array
            bvec = np.zeros(n_elements, dtype=int)
            bvec[idx] = 1
            p = pmf[idx].sum()
            if np.isclose(p, 0):
                zero_elements += bvec

    A = []
    b = []
    zero = []
    for i, is_zero in enumerate(zero_elements):
        if is_zero:
            eq = np.zeros(n_elements, dtype=int)
            eq[i] = 1
            A.append(eq)
            b.append(0)
            zero.append(i)

    if A:
        A = np.asarray(A)
        b = np.asarray(b)
    else:
        A = None
        b = None

    zeroset = set(zero)
    nonzero = [i for i in range(n_elements) if i not in zeroset]
    variables = Bunch(nonzero=nonzero, zero=zero)

    return A, b, variables


def initial_point(dist, k, A=None, b=None, isolated=None, **kwargs):
    """
    Find an initial point in the interior of the feasible set.

    """
    from cvxopt import matrix
    from cvxopt.modeling import variable

    if isolated is None:
        _, __, variables = isolate_zeros(dist, k)

    if A is None or b is None:
        A, b = marginal_constraints(dist, k)

        # Reduce the size of A so that only nonzero elements are searched.
        Asmall = A[:, variables.nonzero]
        Asmall = matrix(Asmall)
        b = matrix(b)
    else:
        # Assume they are already CVXOPT matrices
        if A.size[1] == len(variables.nonzero):
            Asmall = A
        else:
            msg = 'A must be the reduced equality constraint matrix.'
            raise Exception(msg)

    n = len(variables.nonzero)
    x = variable(n)
    t = variable()

    tol = 1e-8

    constraints = []
    constraints.append( (-tol <= Asmall * x - b) )
    constraints.append( ( Asmall * x - b <= tol) )
    constraints.append( (x >= t) )

    # Objective to minimize
    objective = -t

    opt = op_runner(objective, constraints, **kwargs)
    if opt.status != 'optimal':
        raise Exception('Could not find valid initial point.')

    # Grab the optimized x
    optvariables = opt.variables()
    if len(optvariables[0]) == n:
        xopt = optvariables[0].value
    else:
        xopt = optvariables[1].value

    # Turn values close to zero to be exactly equal to zero.
    xopt = np.array(xopt)[:,0]
    xopt[np.abs(xopt) < tol] = 0
    xopt /= xopt.sum()

    # Do not build the full vector since this is input to the reduced
    # optimization problem.
    #xx = np.zeros(len(dist.pmf))
    #xx[variables.nonzero] = xopt

    return xopt, opt


def check_feasibility(dist, k, **kwargs):
    """
    Checks feasibility by solving the minimum residual problem:

        minimize: max(abs(A x - b))

    If the value of the objective is close to zero, then we know that we
    can match the constraints, and so, the problem is feasible.

    """
    from cvxopt import matrix
    from cvxopt.modeling import variable

    A, b = marginal_constraints(dist, k)
    A = matrix(A)
    b = matrix(b)

    n = len(dist.pmf)
    x = variable(n)
    t = variable()

    c1 = (-t <= A * x - b)
    c2 = ( A * x - b <= t)
    c3 = ( x >= 0 )

    objective = t
    constraints = [c1, c2, c3]

    opt = op_runner(objective, constraints, **kwargs)
    if opt.status != 'optimal':
        raise Exception('Not feasible')

    return opt


def frank_wolfe(objective, gradient, A, b, initial_x, maxiters=1000, tol=1e-3, verbose=True):
    """
    Uses the Frank--Wolfe algorithm to minimize the convex objective.

    Assumes x should be nonnegative.

    """
    # All variables should be cvxopt variables, not NumPy arrays
    from cvxopt import matrix
    from cvxopt.modeling import variable

    assert(A.size[1] == initial_x.size[0])

    n = initial_x.size[0]
    x = initial_x
    xdiff = 0

    TOL = 1e-7
    verbosechunk = maxiters / 10
    for i in range(maxiters):
        obj = objective(x)
        grad = gradient(x)

        xbar = variable(n)

        new_objective = grad.T * xbar
        constraints = []
        constraints.append( ( xbar >= 0 ) )
        constraints.append( (-TOL <= A * xbar - b) )
        constraints.append( ( A * xbar - b <= TOL) )

        opt = op_runner(new_objective, constraints, show_progress=False)
        if opt.status != 'optimal':
            msg = "\tDid not find optimal direction on iteration {}: {}"
            print(msg.format(i, opt.status))

        # Calculate optimality gap
        xbar_opt = opt.variables()[0].value
        opt_bd = grad.T * (xbar_opt - x)

        if verbose and i % verbosechunk == 0:
            msg = "i={:6}  obj={:10.7f}  opt_bd={:10.7f}  xdiff={:12.10f}"
            print(msg.format(i, obj, opt_bd[0,0], xdiff))

        xnew = (i * x + 2 * xbar_opt) / (i + 2)
        xdiff = np.linalg.norm(xnew - x)
        x = xnew

        if (xdiff < tol):
            obj = objective(x)
            break
    else:
        msg = "Only converged to xdiff={:12.10f} after {} iterations. Desired: {}"
        print(msg.format(xdiff, maxiters, tol))

    # Cleanup
    xopt = np.array(x)
    xopt[np.abs(xopt) < tol] = 0
    xopt /= xopt.sum()


    return xopt, obj


def negentropy(p):
    """
    Entropy which operates on vectors of length N.

    """
    # This works fine even if p is a n-by-1 cvxopt.matrix.
    return np.nansum(p * np.log2(p))

def marginal_maxent(dist, k, **kwargs):
    from cvxopt import matrix

    _, __, variables = isolate_zeros(dist, k)
    A, b = marginal_constraints(dist, k)

    Asmall = A[:, variables.nonzero]
    Asmall = matrix(Asmall)
    b = matrix(b)

    initial_x, _ = initial_point(dist, k, A=Asmall, b=b, show_progress=False)
    initial_x = matrix(initial_x)
    objective = negentropy

    # We optimize the reduced problem.

    # For the gradient, we are going to keep the elements we know to be zero
    # at zero. Generally, the gradient is: log2(x_i) + 1 / ln(b)
    nonzero = variables.nonzero
    ln2 = np.log(2)
    def gradient(x):
        # This operates only on nonzero elements.

        xarr = np.asarray(x)
        # All of the optimization elements should be greater than zero
        # But occasional they might go slightly negative or zero.
        # In those cases, we will just set the gradient to zero and keep the
        # value fixed from that point forward.
        bad_x = xarr <= 0
        grad = np.log2(xarr) + 1 / ln2
        grad[bad_x] = 0
        return matrix(grad)

    x, obj = frank_wolfe(objective, gradient, Asmall, b, initial_x, **kwargs)
    x = np.asarray(x).transpose()[0]

    # Rebuild the full distribution.
    xfinal = np.zeros(A.shape[1])
    xfinal[nonzero] = x

    return xfinal, obj

def marginal_maxent_dists(dist, k_max=None, maxiters=1000, tol=1e-3, verbose=False):
    """
    Return the marginal-constrained maximum entropy distributions.

    Parameters
    ----------
    dist : distribution
        The distribution used to constrain the maxent distributions.
    k_max : int
        The maximum order to calculate.

    """
    dist = prepare_dist(dist)

    n_variables = dist.outcome_length()
    symbols = dist.alphabet[0]

    if k_max is None:
        k_max = n_variables

    outcomes = list(dist._sample_space)

    # Optimization for the k=0 and k=1 cases are slow since you have to optimze
    # the full space. We also know the answer in these cases.

    k0 = dit.Distribution(outcomes, [1]*len(outcomes), validate=False)
    k0.normalize()

    k1 = dit.product_distribution(dist)


    dists = [k0, k1]
    for k in range(k_max + 1):
        if verbose:
            print()
            print("Constraining maxent dist to match {0}-way marginals.".format(k))

        if k in [0, 1, k_max]:
            continue

        kwargs = {'maxiters': maxiters, 'tol': tol, 'verbose': verbose}
        pmf_opt, opt = marginal_maxent(dist, k, **kwargs)
        d = dit.Distribution(outcomes, pmf_opt)
        d.make_sparse()
        dists.append(d)

    # To match the all-way marginal is to match itself. Again, this is a time
    # savings decision, even though the optimization should be fast.
    dists.append(dist)

    return dists

