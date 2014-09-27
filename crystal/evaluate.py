from collections import defaultdict
from .crystal import model_clusters
import toolshed as ts
import itertools as it
import seaborn as sns
import pandas as pd
import numpy as np
import sys
from sklearn.metrics import roc_curve, auc


def cluster_bediter(modeled_clusters):
    for m in modeled_clusters:
        c = m['cluster']
        yield (c[0].chrom, c[0].position - 1, c[-1].position, m['p'], len(c))

def evaluate(bed_iter, regions, size=None, label='', ax=None, **plot_kwargs):
    """
    Evaluate the accuracy of a method (`model_fn`) by comparing how many
    fall into a set of "truth" regions vs. outside.

    Parameters
    ----------

    bed_iter : iterable
        iterable with each element of (chrom, start, end, p-value, n_sites)
        can be regions or features.

    regions : file
        BED file of regions.

    size : int
        which size region to test. default (None) is all sizes.

    label : str
        label for plot legend

    ax : axis

    """
    # TODO: implement size

    true_regions = defaultdict(list)
    false_regions = defaultdict(list)
    for i, toks in enumerate(ts.reader(regions, header=False)):
        # see if it's a header.
        if i == 0 and not (toks[1] + toks[2]).isdigit(): continue
        # seen used keep track of the regions we've found
        chrom, start, end = toks[0], int(toks[1]), int(toks[2])
        if len(toks) <= 3 or toks[3][:3] == "tru":
            true_regions[chrom].append((start, end))
        else:
            false_regions[chrom].append((start, end))


    seen_true = defaultdict(set)
    seen_false = defaultdict(set)

    def is_in(b, regions, check_regions):
        r = regions[b[0]]
        found = 0
        for s, e in r:
            if b[2] >= s and b[1] <= e:
                # keep track of the regions that have been seen
                check_regions[b[0]].add((s, e))
                found += 1
        return found

    no_false = len(false_regions) == 0

    truths = []
    ps = []
    for b in bed_iter:
        chrom, start, end, p, n_sites = b[:5]
        assert isinstance(b[1], int)
        n_true = is_in(b, true_regions, seen_true)
        # also keep track of which false regions have been seen, but don't use
        # the return value
        if no_false:
            n_false = n_sites - n_true
        else:
            n_false = is_in(b, false_regions, seen_false)

        # can't have this assertion because other tools find things outside the
        # given data.
        #assert n_true + n_false == n_sites, (n_true, n_false, b)

        # here, we multiply because each region can overlap multiple sites
        truths.extend([1] * n_true)
        truths.extend([0] * n_false)
        ps.extend([p] * (n_true + n_false))


    # now we need to add in the missed trues and missed falses.
    #"""
    one = 1 - 1e-16
    for chrom in true_regions:
        regs = set(true_regions[chrom])
        seen = seen_true[chrom]
        assert not seen - regs
        missed = regs - seen
        for s, e in missed:
            # add a p-value of 1 for missed regions
            # do not multiply by length because we have a row for each site in each
            # region already.
            ps.extend([one])
            truths.extend([1])

    # blah code duplication.
    for chrom in false_regions:
        regs = set(false_regions[chrom])
        seen = seen_false[chrom]
        assert not seen - regs
        missed = regs - seen
        for s, e in missed:
            ps.extend([one])
            truths.extend([0])
    #"""
    truths, ps = np.array(truths), np.array(ps)
    if ax is None:
        return truths, ps

    fpr, tpr, _ = roc_curve(truths[~np.isnan(ps)], 1. - ps[~np.isnan(ps)])
    label = ("AUC: %.4f | " % auc(fpr, tpr)) + label
    print label, len(truths)
    ax.plot(fpr[1:], tpr[1:], label=label, **plot_kwargs)
    ax.set_xlabel('1 - specificity')
    ax.set_ylabel('sensitivity')
    return truths, ps

def plot_roc(ax, r, plot_kwargs={}):
    """
    Plot ROC for a given result.

    Parameters
    ----------

    ax: matplotlib axis

    r: dict
        return value from :func:`~evaluate_method`
    """
    t, f = r['true-ps'], r['null-ps']
    t, f = t[~np.isnan(t)], f[~np.isnan(f)]
    truth = np.array([1] * len(t) + [0] * len(f))
    ps = np.concatenate((t, f))
    vals = 1 - ps
    fpr, tpr, _ = roc_curve(truth, vals)
    label = ("AUC: %.4f | " % auc(fpr, tpr)) + r['label']
    ax.plot(fpr[1:], tpr[1:], label=label, **plot_kwargs)
    ax.set_xlabel('1 - specificity')
    ax.set_ylabel('sensitivity')

def plot_pvalue_grid(results):
    from matplotlib import pyplot as plt
    # http://web.stanford.edu/~mwaskom/software/seaborn/tutorial/axis_grids.html
    grid = np.array([r['null-ps'] for r in results]).T
    grid = pd.DataFrame(grid, columns=[r.get('label', r['method']) for r in results])

    g = sns.PairGrid(grid, diag_sharey=True)#, hue='truth')

    #g.map_diag(plt.hist)
    g.map_offdiag(lambda x, y, *args, **kwargs: plt.scatter(-np.log10(x),
                                                            -np.log10(y), *args, **kwargs))
    return g


def plot_times(ax, results, labels, colors=None):
    """
    Plot the times taken for each result in results
    from :func:`evalute_method`

    Parameters
    ----------

    ax: matplotlib axis

    results: list of list of dict
        list of return values from :func:`~model_clusters`

    colors: list
        list of colors for matplotlib

    """

    if colors is None:
        colors = sns.color_palette("Set1", len(results))

    tmax = int(0.5 + np.log10(1 + max(float(u['time']) for m in results for u in m) / 60.))
    for i, m in enumerate(results):
        t = sum(u['time'] for u in m) / 60.
        s = ax.bar(i + 0.08, height=np.log10(1 + t), width=0.85, fc=colors[i],
                        label=labels[i] + ' - %.1f min.' % t)
    ax.legend(loc="best")

    ax.set_yticklabels(["%i" % (10**i) for i in range(tmax)])
    ax.set_title('CPU time (minutes)')
    ax.set_xticks([])

def write_modeled_regions(modeled_clusters, p_cutoff, out_fh):
    """
    Write a region bed file suitable for use in :func:`~evaluate_modeled_regions`.

    Parameters
    ----------

    modeled_clusters : list
        output from :func:`~model_clusters`

    p_cutoff : float
        values < this are set as true

    out_fh : filehandle
        where to write the data
    """
    fmt = "{chrom}\t{start}\t{end}\t{truth}\t{size}\n"
    out_fh.write(ts.fmt2header(fmt))
    for mc in modeled_clusters:
        c = mc['cluster']
        truth = ['false', 'true'][int(mc['p'] < p_cutoff)],
        for f in c:
            out_fh.write(fmt.format(**dict(chrom=f.chrom, start=f.position - 1,
                         end=f.position,
                         truth=truth,
                         size=len(c))))
