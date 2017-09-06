"""
Overview
--------

This module contains a routines to convert the output of various
`sklearn.datasets.make_*` functions, like

    sklearn.datasets.make_blobs
    sklearn.datasets.make_regression
    sklearn.datasets.make_classification

to the following containers

    xarray.Dataset
    numpy.array
    pandas.DataFrame

as well as allowing us to do some postprocessing of the data conveniently.

The idea is to provide drop-in replacements for the `sklearn.make_*` functions
but with optional keyword arguments, chiefly among them `astype` that defines
the type of the data structure, as well as other keywords (that interact with
the `astype` keyword argument).

Note: we focus on the `make_*` functions that output a tuple (X, y)
where

    X is a n_samples by n_features numpy.array of features
    y is a n_samples numpy.array of labels
    make_f can be called without any user-supplied arguments (default only)

For example, in scikit-levar version 0.18.1, the following `make_*` routines
can be called with defaults only, but do _not_ return X, y:

- `make_low_rank_matrix`
- `make_sparse_spd_matrix`

The following `make_*` functions cannot be called with defaults only:

- `make_biclusters`
- `make_checkerboard`
- `make_sparse_coded_signal`
- `make_spd_matrix`


Why this is useful
------------------

The reasons to wrap the sklearn routines are:

- We want new types easily (xarray.Dataset, etc.)
- We want multidimensional features.
- We want to generate sample weights together with synthetic data.

Motivating usecases:

- Generate data to test models that predict temperature or humidity at
  3d-spatial coordinates and time based on earlier measurements at nearby/same
  coordinates.

Approach
--------

The idea here is to redefine in this module the sklearn functions mentioned
above. Each one of the new functions has the same signature as in sklearn, as
well as some additional, optional keywords, chiefly among them `astype`. 

If `astype=None`, then the `make_*` function behaves just like in sklearn, 
but returns a XyTransformer object that has various methods to postprocess that
(X, y) data. For example, you can do

>>> m = make_blobs(astype=None)
>>> m.to_dataframe(xnames=['feat1', 'feat2'], yname='response')

Alternatively, you can do everything in one step

>>> m = make_blobs(astype=pandas.DataFrame, xnames=['feat1', 'feat2'],
        yname='response')

The signature of `make_blobs` will be just like in sklearn, with the additional
explicit keyword `astype`, plus a variable set of keywords `**kwargs`.

The signature of `m.to_dataframe` will have the proper documentation for that
method, without any variable set of arguments or keyword arguments.

By default, `astype=xarray.DataSet` in which case the function behaves exactly
like in sklearn.

TODO: should we have xtype and ytype instead of astype?
"""



import inspect
import string

import numpy as np
import xarray as xr
import pandas as pd
import sklearn.datasets
import logging

from collections import Sequence, OrderedDict, defaultdict
from utils import _infer_coords_and_dims
from functools import partial, wraps

logging.basicConfig()
logger = logging.getLogger(__name__)


class XyTransformer:
    "Transforms a pair (feature_matrix, labels_vector) with to_* methods."
    # Transform methods are to_f where f in accepted types
    accepted_types = ('array', 'dataframe', 'dataset') 

    def __init__(self, X, y=None):
        """Initalizes an XyTransformer object.

        Access the underlying feature matrix X and labels y with self.X and
        self.y, respectively.
        """
        self.X = X  # always a 2d numpy.array
        self.y = y  # always a 1d numpy.array

    def to_array(self, xshape=None):
        "Return X, y NumPy arrays with given shape"
        if xshape:
            X, y = self.X.reshape(xshape), self.y
        else:
            X, y = self.X, self.y
        return X, y

    def to_dataframe(self, xnames=None, yname=None):
        "Return a dataframe with features/labels optionally named."
        df = pd.DataFrame(self.X, columns=xnames)
        df[yname] = self.y
        return df

    def to_dataset(self, coords=None, dims=None, attrs=None, shape=None, xnames=None, yname=None):
        """Return an xarray.DataSet with given shape, coords/dims/var names.

        Parameters
        ----------
        coords : sequence or dict of array_like objects, optional
            Coordinates (tick labels) to use for indexing along each dimension.
            If dict-like, should be a mapping from dimension names to the
            corresponding coordinates.
        dims : str or sequence of str, optional
            Name(s) of the the data dimension(s). Must be either a string (only
            for 1D data) or a sequence of strings with length equal to the
            number of dimensions. If this argument is omitted, dimension names
            are taken from ``coords`` (if possible) and otherwise default to
            ``['dim_0', ... 'dim_n']``.
        attrs : dict_like or None, optional
            Attributes to assign to the new instance. By default, an empty
            attribute dictionary is initialized.
        shape: tuuple, optional
            Length of each dimension, or equivalently, number of elements in each
            coordinate.
        xnames : sequence of str, optional
            Name given to each feature (column in self.X).
        yname : str, optional
            Name given to the label variable (self.y).

        Returns
        -------
        dataset = xarra.Dataset
            Each feature (column of self.X) and the label (self.y) becomes a
            data variable in this dataset.


        """
        # Obtain coordinates, dimensions, shape, variable names, etc.
        if not shape:
            shape = (self.X.shape[0],)
        new_coords, new_dims = _infer_coords_and_dims(shape, coords, dims)
        nvars = self.X.shape[1]
        if not xnames:
            xnames = ['X' + str(n) for n in range(nvars)]
        # store features X in dataset
        ds = xr.Dataset(attrs=attrs)
        for (xname, col) in zip(xnames, self.X.T):
            ds[xname] = xr.DataArray(data=col.reshape(shape), coords=new_coords, dims=new_dims)
        # store label y
        if not yname:
            yname = 'y'
        ds[yname] = xr.DataArray(data=self.y.reshape(shape), coords=new_coords, dims=new_dims)
        return ds

    def to_ml_features(self, layers=None, shape=None, dims=None, **features_kw):
        raise NotImplementedError  # waiting on MLDataset availability
        #dset, _ = self.to_dataset(layers=layers, shape=shape, dims=dims)
        ## TODO - This won't work until PR 3 merge and change  # ADDRESS AFTER MERGING INTO MASTER
        ## is made in above TODO note on MLDataset
        #dset = dset.to_ml_features(**features_kw)
        #return dset, self.y

    def astype(self, to_type, **kwargs):
        """Convert to given type.

        self.astype(f, **kwargs) calls self.to_f(**kwargs)

        Valid types are in XyTransformer.accepted_types.

        See Also
        --------

        XyTransformer.to_dataset
        XyTransformer.to_array
        XyTransformer.to_dataframe
        XyTransformer.to_*
        etc...
        """
        assert to_type in self.__class__.accepted_types
        to_method_name = 'to_' + to_type
        to_method = self.__getattribute__(to_method_name)
        return to_method(**kwargs)


def _make_base(skl_sampler_func):
    """Maps a make_* function from sklearn to a XyTransformer

    The goal is to use the make_* functions from sklearn to generate the data,
    but then postprocess it with the various to_* methods from a XyTransformer
    class.

    Note: a decorator doesn't solve this. It could add the functionality, but
    it would not change the docstring or the signature of the function, as it
    would keep the original one from the wrapped function from sklearn. So we
    have to do the process manually.

    TODO - Gui - Ensure the default data structures returned are:
      * X -> MLDataset from PR 3 (xr.Dataset for now)   # ADDRESS AFTER MERGING INTO MASTER
      * y -> Should be a numpy 1-D array
        * TODO - Gui - after this PR is merged, make an issue
          in xarray_filters to support different data structures
          for y - eventually y may be a DataArray, Series,
          * See scikit-learn docs - warnings - do we want a 1-D array for
          y into scikit-learn or do we want a 2-D array for y that
          has only 1 column.  (.squeeze?)
            * Try to summarize plan for what shape y should be
              for most methods - we'll need to standardize y   # ADDRESS AFTER MERGING INTO MASTER
              in the final step of any chained transformers    # FOR NOW Y WILL BE 1D
    """
    skl_argspec = inspect.getfullargspec(skl_sampler_func)
    # Here is where we use the assumption that the make_* function from sklearn
    # has all positional or keyword arguments, all of them with defaults; it
    # could be easily adapted to more flexible setups. TODO: make this more
    # robust; users of the library may apply this to functions that do not
    # satisfy the assumptions listed above.
    assert not skl_argspec.varargs, "{} has variable positional arguments".format(skl_sampler_func.__name__)
    assert not skl_argspec.kwonlyargs, "{} has keyword-only arguments".format(skl_sampler_func.__name__)
    assert len(skl_argspec.args) == len(skl_argspec.defaults), \
            "Some args of {} have no default value".format(skl_sampler_func.__name__)
    skl_params = [inspect.Parameter(name=pname, kind=inspect.Parameter.POSITIONAL_OR_KEYWORD, default=pdefault)
                  for (pname, pdefault) in zip(skl_argspec.args, skl_argspec.defaults)]
    default_astype = 'dataset'
    def wrapper(*args, **kwargs):
        '''
        All optional/custom args are keyword arguments.
        '''
        # Step 1: process positional and keyword arguments
        skl_kwds = skl_argspec.args + skl_argspec.kwonlyargs
        # splitting arguments into disjoint sets; astype is a special argument
        skl_kwargs = {k: val for (k, val) in kwargs.items() if k in skl_kwds}
        astype = kwargs.get('astype', default_astype)
        type_kwargs = {k: val for (k, val) in kwargs.items() if (k not in
            skl_kwds and k != 'astype')} 

        # Step 2: obtain the XyTransformer object
        # First we need to check that we can handle the output of skl_sampler_func
        out = skl_sampler_func(*args, **skl_kwargs)
        if len(out) != 2:
            error_msg = 'Function {} must return a tuple of 2 elements'.format(skl_sampler_func.__name__)
            raise ValueError(error_msg)
        else:
            X, y = skl_sampler_func(*args, **skl_kwargs)
            if y.ndim != 1:
                raise ValueError("Y must have dimension 1.")
            if X.shape[0] != y.shape[0]:
                raise ValueError('X and y must have the same number of rows')
        Xyt = XyTransformer(X, y)

        # Step 3: convert the data to the desired type
        if astype is None:
            sim_data = Xyt
        else:
            sim_data = Xyt.astype(to_type=astype, **type_kwargs)

        return sim_data

    # We now have some tasks: (1) fix the docstring and (2) fix the signature of `wrapper`.
    # Task 1 of 2: fixing the docstring of `wrapper`
    preamble_doc = """Like {skl_funcname}, but with added functionality.

    Parameters
    ---------------------
    Same parameters/arguments as {skl_funcname}, in addition to the following
    keyword-only arguments:

    astype: str
        One of {accepted_types} or None to return an XyTransformer. See documentation
        of XyTransformer.astype.
        
    **kwargs: dict
        Optional arguments that depend on astype. See documentation of
        XyTransformer.astype. 

    See Also
    --------
    {skl_funcname}
    {xy_transformer}

    """.format(
            skl_funcname=(skl_sampler_func.__module__ + '.' + skl_sampler_func.__name__),
            xy_transformer=(XyTransformer.__module__ + '.' + XyTransformer.__name__),
            accepted_types=(str(XyTransformer.accepted_types))

    )
    wrapper.__doc__ = preamble_doc # + skl_sampler_func.__doc__
    # Task 2 of 2: fixing the signature of `wrapper`
    astype_param = inspect.Parameter(name='astype', kind=inspect.Parameter.KEYWORD_ONLY, default=default_astype)
    kwargs_param = inspect.Parameter(name='kwargs', kind=inspect.Parameter.VAR_KEYWORD)
    params = skl_params + [astype_param, kwargs_param]
    wrapper.__signature__ = inspect.Signature(params)
    wrapper.__name__ = skl_sampler_func.__name__
    return wrapper


def fetch_lfw_people(*args, **kw):
    '''TODO Gui wrap docs from sklearn equivalent
    and add notes about it returning MLDataset

    out = fetch_lfw_people()
    In [6]: out.ariel_sharon_65
Out[6]:
<xarray.DataArray 'ariel_sharon_65' (row: 62, column: 47)>
array([[ 111.666664,  128.333328,  123.333336, ...,  186.333328,  188.      ,
         188.333328],
       [ 103.      ,  124.666664,  121.      , ...,  184.333328,  186.      ,
         187.333328],
       [  97.      ,  119.333336,  114.666664, ...,  178.333328,  180.333328,
         183.333328],
       ...,
       [  23.666666,   24.      ,   20.      , ...,  182.      ,  192.666672,
         200.      ],
       [  21.333334,   20.666666,   18.      , ...,  191.      ,  201.      ,
         202.666672],
       [  20.666666,   18.      ,   14.666667, ...,  197.333328,  202.      ,
         199.333328]], dtype=float32)
Coordinates:
  * row      (row) int64 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 ...
  * column   (column) int64 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 ...
Attributes:
    id:       373
    name:     Ariel Sharon
    '''
    raise NotImplementedError
    #out = sklearn.datasets.fetch_lfw_people(*args, **kw)
    #dset = OrderedDict()
    #name_tracker = defaultdict(lambda: -1)
    #for img, name_id in zip(out.images, out.target):
    #    name = out.target_names[name_id]
    #    name_tracker[name] += 1
    #    clean_name = name.lower().replace(' ', '_') + '_' + str(name_tracker[name])
    #    r, c = img.shape
    #    coords = OrderedDict([('row', np.arange(r)),
    #                          ('column', np.arange(c))])
    #    dims = ('row', 'column',)
    #    attrs = dict(name=name, id=name_id)
    #    data_arr = xr.DataArray(img, coords=coords, dims=dims, attrs=attrs)
    #    dset[clean_name] = data_arr
    #attrs = dict(metadata=[args, kw])
    ## TODO - PR 3's MLDataset instead of Dataset
    #dset = xr.Dataset(dset, attrs=attrs)
    ## TODO - allow converting to dataframe, numpy array?
    #return dset


# Convert all sklearn functions that admit conversion
converted_make_funcs = dict()  # holds converted sklearn funcs
sklearn_make_funcs = [_ for _ in dir(sklearn.datasets) if _.startswith('make_')]  # conversion candidates
for func_name in sklearn_make_funcs:
    try:
        func = getattr(sklearn.datasets, func_name)
        converted_make_funcs[func_name] = _make_base(func)
    except (ValueError, AssertionError) as e:
        warning_msg = "Cannot convert function {}. ".format(func_name) + str(e)
        logger.warning(warning_msg)
globals().update(converted_make_funcs)  # careful with overwrite here


extras = ['XyTransformer', 'fetch_lfw_people']
__all__ = list(converted_make_funcs) + extras
