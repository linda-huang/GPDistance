#!/usr/bin/env python

"""This module provides functionality implementing an absorbing Markov
chain. It can be used to calculate the expected length of a random
walk between any two points in a space, given the transition matrix on
the space. Also the lowest-cost path (treating low transition
probabilities as high edge traversal costs) and the shortest path (in
number of steps, disregarding probabilities)."""

import numpy as np
import scipy.stats, scipy.misc
import random
import sys
import os
import itertools
from collections import OrderedDict
import matplotlib.pyplot as plt
import pickle

# this import is from
# http://www.pysal.org/library/spatial_dynamics/ergodic.html: it
# implements the mean-first-passage-time algorithm, also known as the
# first-mean-passage-time, as described in Kemeny, John, G. and
# J. Laurie Snell (1976) Finite Markov Chains. Springer-Verlag,
# Berlin.
import ergodic
import tsp

def analyse_random_walk(dirname):
    """Java code will write out a list of sampled lengths of random
    walks between nodes i and j. A single file, each line containing
    the tree i, tree j, then the list of samples. Analyse the
    basics."""

    n = 20
    f = open(dirname + "/MFPT_random_walking_samples.dat")
    x_mean = np.zeros((n, n))
    x_var = np.zeros((n, n))
    x_len = np.zeros((n, n), dtype=int)
    i = 0
    j = 0
    for line in f:
        t0, t1, samples = line.split(":")
        samples = np.array(map(int, samples.strip().split(" ")))
        x_mean[i, j] = np.mean(samples)
        x_var[i, j] = np.std(samples)
        x_len[i, j] = len(samples)
        i += 1
        if i == n:
            i = 0
            j += 1
    print(x_mean)
    print(x_var)
    print(x_len)

def estimate_MFPT_with_supernode(dirname):
    """Given a directory, go in there and get all the files under
    TP_supernode_estimates. For each, run the algorithm to get mfpt,
    and from the result extract the (0, 1) and (1, 0) values. Find out
    which trees they correspond to. Then correlate between all these
    values and the values calculated by exact MFPT given the complete
    TP matrix."""
    n = 50
    all_trees = open(dirname + "/all_trees.dat").read().strip().split("\n")
    estimate = np.zeros(2 * n)
    exact = np.genfromtxt(dirname + "/MFPT.dat")
    exact_extract = np.zeros(2 * n)
    ted = np.genfromtxt(dirname + "/TED.dat")
    ted_extract = np.zeros(2 * n)
    for i in range(n):
        d = read_transition_matrix(dirname + "/TP_supernode_estimates/"
                                   + str(i) + "_TP_estimates.dat")
        m = get_mfpt(d)
        t, s = open(dirname + "/TP_supernode_estimates/"
                    + str(i) + "_trees.dat").read().strip().split("\n")
        ti = all_trees.index(t)
        si = all_trees.index(s)
        estimate[2*i] = m[0, 1]
        estimate[2*i+1] = m[1, 0]
        exact_extract[2*i] = exact[ti, si]
        exact_extract[2*i+1] = exact[si, ti]
        ted_extract[2*i] = ted[ti, si]
        ted_extract[2*i+1] = ted[si, ti]
    np.savetxt(dirname + "/MFPT_supernode_estimate.dat", estimate)
    np.savetxt(dirname + "/TED_extract_for_supernode_estimate.dat", ted_extract)
    np.savetxt(dirname + "/MFPT_exact_for_supernode_estimate.dat", exact_extract)

def normalise_by_row(d):
    """Normalise an array row-by-row, ie make each row sum to 1. This
    is useful when creating transition matrices on sub-graphs, or
    randomly-generating transition matrices, or for matrices created
    using a hill-climbing adjustment to transition probabilies: in
    such cases, the transition probabilities are adjusted to take
    account of fitness, so "bad" transitions (to worse fitness) are
    made less likely to be accepted, meaning that each row no longer
    sums to 1. By renormalising we get the true probability after (if
    necessary) multiple rounds of rejection and finally one
    acceptance."""
    # np.sum(d, 1).reshape((len(d), 1)) is the column of row-sums
    return d / np.sum(d, 1).reshape((len(d), 1))

def make_random_matrix(n):
    """Make a random transition matrix on n points."""
    tm = np.random.random((n, n))
    return normalise_by_row(tm)

def make_random_binary_matrix(n, p):
    """n is the number of nodes, p the probability of each possible
    edge existing. A graph with isolated nodes or isolated
    subcomponents will break the MFPT algorithm, so we have to be
    careful. There are algorithms to guarantee connectivity, but the
    quick-n-dirty solution is to keep generating until we get a
    connected graph. If we fail after (say) 100 attempts, it could be
    because the choice of n and p tend to give unconnected graphs, so
    just fail.
    """

    i = 0
    while i < 100:
        d = np.array(np.random.random((n, n)) < p, dtype=float)
        d = normalise_by_row(d)
        steps = floyd_warshall_nsteps(d)
        if np.all(np.isfinite(steps)):
            return d

def map_infinity_to_large(w):
    """If there any infinities in w, they can cause numerical errors.
    In some situations, it can be adequately solved by putting in an
    arbitrary large value instead. We map to 100 times the largest
    finite value in w."""
    is_w_finite = np.isfinite(w)
    realmax = np.max(w[is_w_finite])
    np.copyto(w, realmax * 100.0, where=~is_w_finite)

def mean_mfpt(n, p):
    """n is the number of nodes, p the probability of each possible
    edge existing. idea is to see if there's a correlation between
    mean mfpt and p."""
    m = make_random_binary_matrix(n, p)
    mfpt = get_mfpt(m)
    mean_mfpt = np.mean(mfpt)
    # get mean mfpt off-diagonal -- this formula works because the
    # diagonal is zero
    mean_mfpt_distinct = np.sum(mfpt) / (len(mfpt)**2 - len(mfpt))
    ne = np.sum(m>0)
    return m, ne, mfpt, mean_mfpt, mean_mfpt_distinct

def test_mean_mfpt():
    reps = 50
    for n in [100]:
        for p in [0.05, 0.1, 0.15, 0.2]:
            for rep in range(reps):
                m, ne, mfpt, mean, mean_distinct = mean_mfpt(n, p)
                print("%d %f" % (ne, mean_distinct))

def make_absorbing(tm, dest):
    """Given a transition matrix, disallow transitions away from the
    given destination -- ie make it an absorbing matrix."""
    e = np.zeros(len(tm))
    e[dest] = 1
    tm[dest, :] = e

def MSTP_wrapper(dirname):
    x = np.genfromtxt(dirname + "/TP.dat")
    for i in [10, 100]:
        mstp = MSTP_max_n_steps(x, i)
        dmstp = -np.log(mstp)
        np.savetxt(dirname + "/D_MSTP_" + str(i) + ".dat", dmstp)

def MSTP_max_n_steps(x, n=10):
    """The probability of reaching state j, starting from state i, in
    n steps or fewer. Loops are allowed, hence even if n > number of
    states, these probabilities don't reach 1 in general."""
    L = len(x)
    mstp = np.eye(L)
    for i in range(L):
        xi = x.copy()
        make_absorbing(xi, i)
        # the ith column is copied from the ith column of A^n, where A
        # is absorbing in state i
        mstp[:, i] = np.linalg.matrix_power(xi, n)[:, i]
    return mstp

def read_transition_matrix(filename):
    """Read a transition matrix from a file and return. The matrix
    will have been written in the right format by some Java code."""
    d = np.genfromtxt(filename)
    return d

def check_row_sums(d):
    """Check that each row sums to 1, since each row is the
    out-probabilities from a single individual. Allow the small margin
    of error used by allclose()."""
    return np.allclose(np.ones(len(d)), np.sum(d, 1))

def is_positive_definite(x):
    """This is supposed to be fairly efficient. From
    http://stackoverflow.com/questions/16266720/find-out-if-matrix-is-positive-definite-with-numpy"""
    try:
        L = np.linalg.cholesky(x)
        return True
    except np.linalg.LinAlgError:
        return False

def kernel_to_distance(k):
    """Given a kernel, ie a symmetric positive definite matrix of
    similarities between elements, produce a distance."""
    if not is_symmetric(k):
        raise ValueError("k is not symmetric")
    if not is_positive_definite(k):
        raise ValueError("k is not positive definite")
    kxx = np.diag(k).reshape((len(k), 1)) * np.ones_like(k)
    kyy = np.diag(k) * np.ones_like(k)
    return np.sqrt(kxx + kyy - k - k.T)

def set_self_transition_zero(x):
    """Set cost/length of self-transition to zero."""
    np.fill_diagonal(x, 0.0)

def get_mfpt(x):
    """Calculate mean-first-passage time of a given transition
    matrix. Set self-transitions to zero. Note that the pysal code
    (ergodic.py) calls it "first-mean-passage-time"."""
    # NB! The ergodic code expects a matrix, not a numpy array. Breaks
    # otherwise.
    x = np.matrix(x)
    x = np.array(ergodic.fmpt(x))
    set_self_transition_zero(x)
    return x

def test_matrix_size(n):
    """Test how big the tm can be before get_mfpt becomes
    slow. n = 4000 is fine, n = 10000 starts paging out (at least 30
    minutes)."""
    d = make_random_matrix(n)
    mfpt = get_mfpt(d)
    print("min", np.min(mfpt))
    print("max", np.max(mfpt))

def invert_probabilities(adj):
    """Convert a probability into an edge traversal cost. It's ok to
    ignore runtime floating point problems here, because they're only
    due to zero-probabilities, which result in infinite edge-traversal
    costs, which is what we want. Restore the "raise" behaviour
    afterward."""
    old = np.seterr(divide='ignore')
    retval = -np.log(adj)
    np.seterr(**old)
    return retval

def deinvert_probabilities(adj):
    """Convert an edge traversal cost into a probability."""
    return np.exp(-adj)

def test_floyd_warshall_random_data(n):
    """Test."""
    adj = make_random_matrix(n)
    return floyd_warshall_probabilities(adj)

def floyd_warshall_probabilities(adj):
    """For this to be useful, need to invert the transition matrix
    probabilities p somehow, so that low probabilities cause high edge
    traversal costs. See invert_ and deinvert_probabilities."""
    x = invert_probabilities(adj)
    x = floyd_warshall(x)
    set_self_transition_zero(x)
    return x

def floyd_warshall(adj):
    """Finds the shortest path between all pairs of nodes. For this to
    be useful, the edge weights have to have the right semantics: a
    small transition probability implies a large edge weight
    (cost). So if your edge weights are probabilities, use
    floyd_warshall_probabilities() instead: it performs the
    conversion.

    Concerning matrix size: n = 1000 works fine, n = 4000 starts
    paging out (at least 10 minutes) on my machine."""
    # from
    # http://www.depthfirstsearch.net/blog/2009/12/03/computing-all-shortest-paths-in-python/
    n = len(adj)
    for k in range(n):
        adj = np.minimum(adj, np.add.outer(adj[:,k],adj[k,:]))
    return adj


def floyd_warshall_nsteps(adj):
    """Disregard the transition probabilities, other than to see
    whether an edge traversal is allowed or not. Calculate the number
    of steps required to get from each point to each other."""
    n = adj.shape[0]
    x = discretize_probabilities(adj)
    x = floyd_warshall(x)
    set_self_transition_zero(x)
    return x

def discretize_probabilities(d):
    """Set the edge cost to 1 if there is a nonzero probability, and to
    infinity if there is a zero probability."""
    retval = np.ones_like(d, dtype=np.float)
    n = d.shape[0]
    retval[d <= 0.0] = np.infty
    return retval

def get_dtp(t):
    """Get D_TP, the distance based on transition probability. D_TP(x,
    y) is the log of TP(x, y), for x != y."""
    x = invert_probabilities(t)
    set_self_transition_zero(x)
    return x

def get_symmetric_version(m):
    """Given an asymmetric matrix, return the symmetrized version,
    which is the mean of the matrix and its transpose."""
    return 0.5 * (m + m.T)

def write_symmetric_remoteness(dirname):
    """Read in the D_TP matrix and the MFPT one, and write out the
    symmetric versions."""
    dtp = np.genfromtxt(dirname + "/D_TP.dat")
    mfpt = np.genfromtxt(dirname + "/MFPT.dat")
    sdtp = get_symmetric_version(dtp)
    ct = get_symmetric_version(mfpt)
    # SD_TP is symmetric transition probability distance
    np.savetxt(dirname + "/SD_TP.dat", sdtp)
    # CT stands for commute time
    np.savetxt(dirname + "/CT.dat", ct)

def get_steady_state(tp):
    """Given a transition probability matrix, use ergodic.steady_state
    to calculate the long-run steady-state, which is a vector
    representing how long the system will spend in each state in the
    long run. If not uniform, that is a bias imposed by the operator
    on the system."""
    import ergodic
    ss = np.array(ergodic.steady_state(np.matrix(tp)))
    ss = np.real(ss) # discard zero imaginary parts
    ss = ss.T[0] # not sure why it ends up with an extra unused dimension
    return ss

def is_symmetric(x):
    return np.allclose(x, x.T)

def exploitativeness_KL(x):
    """Measure exploitativeness as the mean KL between rows of x and rows
    of the uniform distribution on same points. But if we know x is
    symmetric, ie each row is just a permutation of the previous, then
    pass in a single row and just calculate KL once."""
    if len(x.shape) == 1:
        # x is a row, so calculate KL(x, y) directly
        y = np.ones_like(x) / float(x.shape[0])
        return scipy.stats.entropy(x, y)
    else:
        # x is a matrix, so calculate mean of KL(xi, y) for each row
        # xi
        y = np.ones_like(x[0]) / float(x.shape[0])
        return np.mean([scipy.stats.entropy(xi, y) for xi in x])

def RMSE(x, y):
    return np.sqrt(np.mean((x - y)**2))

def operator_difference_RMSE(x, y):
    """The difference between two mutation operators could be measured as
    the RMSE of their transition matrices.

    """
    return np.sqrt(np.mean((x - y)**2))

def operator_difference_KL(x, y):
    """The difference between two mutation operators could be measured as
    the symmetrized Kullback-Leibler divergence between corresponding
    rows, then the mean across rows. KL is not symmetric, but we can
    take the mean of the two directions. However, with this method we
    will get infinite results whenever there are zeros in the y, so
    this method is not really useful in practice."""
    kl_xy = np.mean([scipy.stats.entropy(xi, yi) for xi, yi in zip(x, y)])
    kl_yx = np.mean([scipy.stats.entropy(yi, xi) for xi, yi in zip(x, y)])
    return 0.5 * (kl_xy + kl_yx)

def compound_operator(wts, ops):
    """A compound operator emulates the behaviour of variable
    neighbourhood search: we first choose an operator according to
    probabilities, then we run the operator. The resulting matrix is
    just the weighted sum of the basic operators' matrices."""
    return sum((wt * op for (wt, op) in zip(wts, ops)))

def read_and_get_dtp_mfpt_sp_steps(dirname):

    if os.path.exists(dirname + "/TP_nonnormalised.dat"):
        t = read_transition_matrix(dirname + "/TP_nonnormalised.dat")
        # it's a non-normalised matrix, possibly representing a
        # uniformly sampled sub-graph, a hill-climb-sampled graph, or
        # similar.
        t = normalise_by_row(t)
        outfilename = dirname + "/TP.dat"
        np.savetxt(outfilename, t)
    else:
        t = read_transition_matrix(dirname + "/TP.dat")
        check_row_sums(t)

    # This gets D_TP, which is just the transition probability inverted
    d = get_dtp(t)
    outfilename = dirname + "/D_TP.dat"
    np.savetxt(outfilename, d)

    # This gets the mean first passage time, ie the expected length of
    # a random walk.
    f = get_mfpt(t)
    outfilename = dirname + "/MFPT.dat"
    np.savetxt(outfilename, f)

    # This gets the cost of the shortest path between pairs. The cost
    # of an edge is the negative log of its probability.
    h = floyd_warshall_probabilities(t)
    outfilename = dirname + "/SP.dat"
    np.savetxt(outfilename, h)

    # this gets the minimum number of steps required to go between
    # pairs, disregarding probabilities. Only interesting if some
    # edges are absent (ie edge probability is zero).
    p = floyd_warshall_nsteps(t)
    outfilename = dirname + "/STEPS.dat"
    np.savetxt(outfilename, p)



def simulate_random_walk(f, nsteps, selected, nsaves):
    """f is the transition function. nsteps is the number of steps.
    selected is the set of states (can be a list of integers) in which
    we're interested. nsaves is the max number of samples to save for
    each pair. Return an array of samples for MFPT for each pair (i,
    j) from selected."""

    n = len(selected)
    # samples is all nans to start
    samples = np.nan + np.zeros((n, n, nsaves))
    # in the rw_started matrix, the (i,j)th entry is the time-step at
    # which a walk from i to j began. -1 means we are not currently in
    # a walk from i to j. can't be in a walk from i to j and from j to
    # i simultaneously.
    rw_started = -1 * np.ones((n, n), dtype='int64')
    # for each transition there are nsaves spaces to save samples --
    # this says where to save
    saveidx = np.zeros((n, n), dtype='int')

    def print_state(v, rw_started, samples, t):
        print("%d: %d" % (t, v))

    # su and sv are states (may be integers). u and v are the indices
    # into selected of su and sv
    sv = selected[0]
    for t in range(nsteps):
        # print_state(sv, rw_started, samples, t)

        # when we see a state sv which is of interest
        if sv in selected:
            v = selected.index(sv)

            for u, su in enumerate(selected):

                if rw_started[u,v] == rw_started[v,u] == -1:
                    # never saw u or v before, but this is the
                    # beginning of a walk from v to u
                    rw_started[v,u] = t

                elif rw_started[u,v] > -1:
                    # we are currently in a walk from u to v, and we
                    # have just reached v, so save a sample (now -
                    # start of walk), if there is space
                    if saveidx[u,v] < nsaves:
                        samples[u,v,saveidx[u,v]] = (t - rw_started[u,v])
                        # update the space left
                        saveidx[u,v] += 1
                    # now we start a walk from v to u. because of the
                    # order of these two assignments, this does the
                    # right thing if v == u.
                    rw_started[u,v] = -1
                    rw_started[v,u] = t

                elif rw_started[v,u] > -1:
                    # we are in a walk from v to u, and have
                    # re-encountered v. ignore.
                    pass

                else:
                    raise InternalError

        # transition to a new state
        sv = f(sv)
    return samples

def roulette_wheel(a):
    """Randomly sample an index, weighted by the given sequence of
    probabilities."""
    s = np.sum(a)
    assert s > 0
    r = random.random() * s
    cumsum = 0.0
    for i, v in enumerate(a):
        cumsum += v
        if r < cumsum:
            return i
    raise ValueError("Unexpected: failed to find a slot in roulette wheel")

def random_search(fitvals, steps, allow_repeat=True):
    """Allow replacement, for this particular experiment."""
    if allow_repeat:
        samples = [random.choice(range(len(fitvals))) for i in range(steps)]
    else:
        samples = random.sample(range(len(fitvals)), steps)
    fitness_samples = [fitvals[sample] for sample in samples]
    return samples, fitness_samples, min(fitness_samples)

def random_walk(tp, steps):
    s = random.randint(0, len(tp)-1)
    samples = []
    for i in range(steps):
        t = roulette_wheel(tp[s])
        samples.append(s)
    return samples

def hillclimb(tp, fitvals, steps, rw=False):
    s = random.randint(0, len(fitvals)-1)
    samples = []
    fitness_samples = []
    fitval = fitvals[s]
    for i in range(steps):
        t = roulette_wheel(tp[s])
        if rw:
            s = t
            fitval = fitvals[t]
        else:
            # note we are maximising!
            if fitvals[t] > fitval:
                s = t
                fitval = fitvals[t]
        samples.append(s)
        fitness_samples.append(fitval)
    return samples, fitness_samples, fitval

def generate_oz_tm_mfpte(dirname):
    tp = land_of_oz_matrix()
    samples = simulate_random_walk(
        lambda i: roulette_wheel(tp[i]), # transition according to tp
        200, [0, 1, 2], 100)
    mfpte = scipy.stats.nanmean(samples, axis=2)
    mfpte_std = scipy.stats.nanstd(samples, axis=2)
    mfpt = get_mfpt(tp)

    np.savetxt(dirname + "/TP.dat", tp)
    np.savetxt(dirname + "/MFPTE.dat", mfpte)
    np.savetxt(dirname + "/MFPTE_STD.dat", mfpte_std)

def uniformify(tp, p):
    return (tp**p) / (np.sum(tp**p, 1).reshape((len(tp), 1)))

def land_of_oz_matrix():
    """From Kemeny & Snell 1976. The states are rain, nice and
    snow."""
    return np.array([[.5, .25, .25], [.5, .0, .5], [.25, .25, .5]])

def SP_v_MFPT_example_matrices():
    return (np.array([[.1, .5, .4], [.1, .8, .1], [.8, .1, .1]]),
            np.array([[.1, .5, .4], [.1, .8, .1], [.1, .8, .1]]))

def permute_vals(v, k):
    """v is a list of values. We permute by swapping pairs, k times.
    We copy v first, to avoid mutating the original."""
    v = v[:]
    L = len(v)
    for x in range(k):
        i, j = random.randint(0, L-1), random.randint(0, L-1)
        v[i], v[j] = v[j], v[i]
    return v

def mu_sigma(t):
    """Calculate mean(stddev_1(t)) and stddev(stddev_1(t)), ie the
    mean of row stddevs and the stddev of row stddevs."""
    sigma = np.std(t, 1)
    return np.mean(sigma), np.std(sigma)

def coefficient_of_variation(r):
    """Calculate coefficient of variation for a row (r) of the transition
    matrix. CV = SD/M, where SD is the standard deviation and M the mean
    of the row. In our case the mean transition probability is 1/N so CV = N.SD."""

    return len(r) * np.std(r)

def mu_sigma_cv(t):
    return (np.mean([coefficient_of_variation(r) for r in t]),
            np.std([coefficient_of_variation(r) for r in t]))

def gini_coeff(x):

    """A measure of inequality in a distribution. From
    http://www.ellipsix.net/blog/2012/11/the-gini-coefficient-for-distribution-inequality.html"""
    # requires all values in x to be zero or positive numbers,
    # otherwise results are undefined
    n = len(x)
    s = x.sum()
    r = np.argsort(np.argsort(-x)) # calculates zero-based ranks
    return 1 - (2.0 * (r*x).sum() + s)/(n*s)

def mean_gini_coeff(t):
    return np.mean([gini_coeff(ti) for ti in t])

def mu_sigma_GINI(t):
    GINI = np.array([gini_coeff(ti) for ti in t])
    return np.mean(GINI), np.std(GINI)

def normalised_SD_expl(expl, N):
    return expl / SD_deterministic_operator(N)

def normalised_KL_expl(expl, N):
    return expl / KL_deterministic_operator(N)

def normalised_Gini_expl(expl, N):
    return expl / Gini_deterministic_operator(N)

def SD_deterministic_operator(N):
    xbar = 1.0 / N
    # The sum of squared errors. There is one entry 1, with error (1 -
    # xbar), and N-1 entries 0, each with error (0 - xbar).
    ssqe = 1 * ((1 - xbar)) ** 2 + (N - 1) * ((0 - xbar) ** 2)
    return np.sqrt(ssqe / float(N))

def KL_deterministic_operator(N):
    # KL(p||q) where q is the uniform operator and p is the
    # deterministic one, in a space of size N.
    #
    # KL(p||q) = \sum_x p(x) \log \frac{p(x)}{q(x)}.
    #
    # If p is the determinstic operator, then all elements p(x) are
    # zero but one, so the sum becomes a single element, where p(x) =
    # 1, q(x) = 1/N. The fraction p/q = 1/(1/N) = N. Some sources use
    # log_2, but scipy.stats.entropy seems to use ln.
    return math.log(N)

def Gini_deterministic_operator(N):
    return gini_m_equal_neighbours_fn(N, 1)

def gini_m_equal_neighbours_fn(N, m):
    """Gini coefficient for a distribution of N points where all elements
    are 0, except for m elements with equal probability."""

    # we normalise so that the width and height of the chart are 1
    m /= float(N)
    N /= float(N)

    T = 1/2.0 # area under the 45-degree line of perfect equality

    # B is the area below the Lorentz curve
    # B is zero, up till the last *m* points, then it goes up linearly,
    # so the area is a right triangle whose adjacent sides
    # (the width and height) are m and N
    B = m * N / 2.0

    A = T - B # by definition
    G = A / (A+B) # by definition
    return G


def detailed_balance(tp, s=None):
    """Calculate whether a given chain has the detailed balance
    condition, that is given a transition matrix tp, with stationary
    state s, that s_i tp_ij = sj tp_ji."""
    if s is None:
        s = get_steady_state(tp)
    # define matrix f (f for flux) by f_ij = s_i tp_ij, and then check
    # if it's symmetric
    f = tp * s.reshape((len(s), 1))
    return is_symmetric(f)

if __name__ == "__main__":
    dirname = sys.argv[1]

    print("doing TP")
    if "depth" in dirname:
        # Matrices have already been generated by Java code.
        pass
    elif "ga" in dirname:
        if "per_ind" in dirname:
            ga_tm_wrapper(dirname)
        else:
            ga_tm_wrapper(dirname, 0.1)
    elif "land_of_oz" in dirname:
        generate_oz_tm_mfpte(dirname)
    elif "tsp" in dirname:
        if "2opt" in dirname:
            tsp.tsp_tm_wrapper(dirname, move="two_opt")
        elif "2hopt" in dirname:
            tsp.tsp_tm_wrapper(dirname, move="twoh_opt")
        elif "3opt_broad" in dirname:
            tsp.tsp_tm_wrapper(dirname, move="three_opt_broad")
        elif "3opt" in dirname:
            tsp.tsp_tm_wrapper(dirname, move="three_opt")
        elif "swap_adj" in dirname:
            tsp.tsp_tm_wrapper(dirname, move="swap_adj")
        elif "swap" in dirname:
            tsp.tsp_tm_wrapper(dirname, move="swap")
        else:
            raise ValueError("Unexpected dirname " + dirname)
    print("doing DTP MFPT SP STEPS")
    read_and_get_dtp_mfpt_sp_steps(dirname)
    print("doing SDTP CT")
    write_symmetric_remoteness(dirname)
    # estimate_MFPT_with_supernode(dirname)
    # analyse_random_walk(dirname)
    # test_random_walk()
    print("doing MSTP")
    MSTP_wrapper(dirname)
