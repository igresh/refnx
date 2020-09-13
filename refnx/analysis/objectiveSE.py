# -*- coding: utf-8 -*-
"""
Created on Sun Sep 13 10:08:08 2020

@author: Isaac
"""


import numpy as np
from numpy.linalg import LinAlgError

from scipy.optimize._numdiff import approx_derivative


from refnx.analysis.objective import BaseObjective
from refnx.dataset import DataSE

from refnx.analysis import (
    is_parameter,
    Parameter,
    possibly_create_parameter,
    is_parameters,
    Parameters,
    Interval,
    PDF,
)
from refnx._lib import unique as f_unique
from refnx._lib import flatten, approx_hess2




class ObjectiveSE(BaseObjective):
    """
    Objective function for using with curvefitters such as
    `refnx.analysis.curvefitter.CurveFitter`.

    Parameters
    ----------
    model : refnx.analysis.Model
        the generative model function. One can also provide an object that
        inherits `refnx.analysis.Model`.
    data : refnx.dataset.Data1D
        data to be analysed.
    lnsigma : float or refnx.analysis.Parameter, optional
        Used if the  experimental uncertainty (`data.y_err`) underestimated by
        a constant fractional amount. The experimental uncertainty is modified
        as:

        `s_n**2 = y_err**2 + exp(lnsigma * 2) * model**2`

        See `Objective.logl` for more details.
    use_weights : bool
        use experimental uncertainty in calculation of residuals and
        logl, if available. If this is set to False, then you should also
        set `self.lnsigma.vary = False`, it will have no effect on the fit.
    transform : callable, optional
        the model, data and data uncertainty are transformed by this
        function before calculating the likelihood/residuals. Has the
        signature `transform(data.x, y, y_err=None)`, returning the tuple
        (`transformed_y, transformed_y_err`).
    logp_extra : callable, optional
        user specifiable log-probability term. This contribution is in
        addition to the log-prior term of the `model` parameters, and
        `model.logp`, as well as the log-likelihood of the `data`. Has
        signature:
        `logp_extra(model, data)`. The `model` will already possess
        updated parameters. Beware of including the same log-probability
        terms more than once.
    name : str
        Name for the objective.

    Notes
    -----
    For parallelisation `logp_extra` needs to be picklable.

    """

    def __init__(
        self,
        model,
        data,
        lnsigma=None,
        use_weights=True,
        transform=None,
        logp_extra=None,
        name=None,
    ):
        self.model = model
        # should be a DataSE instance
        if isinstance(data, DataSE):
            self.data = data
        else:
            print('bad')
            self.data = DataSE(data=data)

        self.lnsigma = lnsigma
        if lnsigma is not None:
            self.lnsigma = possibly_create_parameter(lnsigma, "lnsigma")

        self._use_weights = use_weights
        self.transform = transform
        self.logp_extra = logp_extra
        self.name = name
        if name is None:
            self.name = id(self)

    def __str__(self):
        s = ["{:_>80}".format("")]
        s.append("Objective - {0}".format(self.name))

        # dataset name
        if self.data.name is None:
            s.append("Dataset = {0}".format(self.data))
        else:
            s.append("Dataset = {0}".format(self.data.name))

        s.append("datapoints = {0}".format(self.npoints))
        s.append("chi2 = {0}".format(self.chisqr()))
        s.append("Weighted = {0}".format(self.weighted))
        s.append("Transform = {0}".format(self.transform))
        s.append(str(self.parameters))

        return "\n".join(s)

    def __repr__(self):
        return (
            "Objective({model!r}, {data!r},"
            " lnsigma={lnsigma!r},"
            " use_weights={_use_weights},"
            " transform={transform!r},"
            " logp_extra={logp_extra!r},"
            " name={name!r})".format(**self.__dict__)
        )

    @property
    def weighted(self):
        """
        **bool** Does the data have weights (`data.y_err`), and is the
        objective using them?

        """
        return self.data.weighted and self._use_weights

    @weighted.setter
    def weighted(self, use_weights):
        self._use_weights = bool(use_weights)

    @property
    def npoints(self):
        """
        **int** the number of points in the dataset.

        """
        return self.data.y.size

    def varying_parameters(self):
        """
        Returns
        -------
        varying_parameters : refnx.analysis.Parameters
            The varying Parameter objects allowed to vary during the fit.

        """
        # create and return a Parameters object because it has the
        # __array__ method, which allows one to quickly get numerical values.
        p = Parameters()
        p.data = list(f_unique(p for p in flatten(self.parameters) if p.vary))
        return p

    def generative(self, pvals=None):
        """
        Calculate the generative (dependent variable) function associated with
        the model.

        Parameters
        ----------
        pvals : array-like or refnx.analysis.Parameters
            values for the varying or entire set of parameters

        Returns
        -------
        model : np.ndarray

        """
        self.setp(pvals)
        return self.model(self.data.x, x_err=self.data.x_err)

    def residuals(self, pvals=None):
        """
        Calculates the residuals for a given fitting system.

        Parameters
        ----------
        pvals : array-like or refnx.analysis.Parameters
            values for the varying or entire set of parameters

        Returns
        -------
        residuals : np.ndarray
            Residuals, `(data.y - model) / y_err`.

        """
        self.setp(pvals)

        psi, delta = self.model(self.data.aoi)
        # TODO add in varying parameter residuals? (z-scores...)


        return np.squeeze(self.data.psi - psi + self.data.delta - delta)

    def chisqr(self, pvals=None):
        """
        Calculates the chi-squared value for a given fitting system.

        Parameters
        ----------
        pvals : array-like or refnx.analysis.Parameters
            values for the varying or entire set of parameters

        Returns
        -------
        chisqr : np.ndarray
            Chi-squared value, `np.sum(residuals**2)`.

        """
        # TODO reduced chisqr? include z-scores for parameters? DOF?
        self.setp(pvals)
        res = self.residuals(None)
        return np.dot(res, res)

    @property
    def parameters(self):
        """
        :class:`refnx.analysis.Parameters`, all the Parameters contained in the
        fitting system.

        """
        if is_parameter(self.lnsigma):
            return self.lnsigma | self.model.parameters
        else:
            return self.model.parameters

    def setp(self, pvals):
        """
        Set the parameters from pvals.

        Parameters
        ----------
        pvals : array-like or refnx.analysis.Parameters
            values for the varying or entire set of parameters

        """
        if pvals is None:
            return

        # set here rather than delegating to a Parameters
        # object, because it may not necessarily be a
        # Parameters object
        _varying_parameters = self.varying_parameters()
        if len(pvals) == len(_varying_parameters):
            for idx, param in enumerate(_varying_parameters):
                param.value = pvals[idx]
            return

        # values supplied are enough to specify all parameter values
        # even those that are repeated
        flattened_parameters = list(flatten(self.parameters))
        if len(pvals) == len(flattened_parameters):
            for idx, param in enumerate(flattened_parameters):
                param.value = pvals[idx]
            return

        raise ValueError(
            f"Incorrect number of values supplied ({len(pvals)})"
            f", supply either the full number of parameters"
            f" ({len(flattened_parameters)}, or only the varying"
            f" parameters ({len(_varying_parameters)})."
        )

    def prior_transform(self, u):
        """
        Calculate the prior transform of the system.

        Transforms uniform random variates in the unit hypercube,
        `u ~ uniform[0.0, 1.0)`, to the parameter space of interest, according
        to the priors on the varying parameters.

        Parameters
        ----------
        u : array-like
            Size of the varying parameters

        Returns
        -------
        pvals : array-like
            Scaled parameter values

        Notes
        -----
        If a parameter has bounds, `x ~ Unif[-10, 10)` then the scaling from
        `u` to `x` is done as follows:

        .. code-block:: python

            x = 2. * u - 1.  # scale and shift to [-1., 1.)
            x *= 10.  # scale to [-10., 10.)

        """
        var_pars = self.varying_parameters()
        pvals = np.empty(len(var_pars), dtype=np.float64)

        for i, var_par in enumerate(var_pars):
            pvals[i] = var_par.bounds.invcdf(u[i])

        return pvals

    def logp(self, pvals=None):
        """
        Calculate the log-prior of the system.

        Parameters
        ----------
        pvals : array-like or refnx.analysis.Parameters
            values for the varying or entire set of parameters

        Returns
        -------
        logp : float
            log-prior probability

        Notes
        -----
        The log-prior is calculated as:

        .. code-block:: python

            logp = np.sum(param.logp() for param in
                             self.varying_parameters())

        """
        self.setp(pvals)

        logp = np.sum(
            [
                param.logp()
                for param in f_unique(
                    p for p in flatten(self.parameters) if p.vary
                )
            ]
        )

        if not np.isfinite(logp):
            return -np.inf

        return logp

    def logl(self, pvals=None):
        """
        Calculate the log-likelhood of the system.

        The major component of the log-likelhood probability is from the data.
        Extra potential terms are added on from the Model, `self.model.logp`,
        and the user specifiable `logp_extra` function.

        Parameters
        ----------
        pvals : array-like or refnx.analysis.Parameters
            values for the varying or entire set of parameters

        Returns
        -------
        logl : float
            log-likelihood probability

        Notes
        -----
        The log-likelihood is calculated as:

        .. code-block:: python

            logl = -0.5 * np.sum(((y - model) / s_n)**2
                                 + np.log(2 * pi * s_n**2))
            logp += self.model.logp()
            logp += self.logp_extra(self.model, self.data)

        where

        .. code-block:: python

            s_n**2 = y_err**2 + exp(2 * lnsigma) * model**2

        """
        self.setp(pvals)

        # Not implimented! Not sure if you can do this when you don't have
        # any y errors.
        
        raise Exception ('logl is not implimented')

    def nll(self, pvals=None):
        """
        Negative log-likelihood function

        Parameters
        ----------
        pvals : array-like or refnx.analysis.Parameters
            values for the varying or entire set of parameters

        Returns
        -------
        nll : float
            negative log-likelihood (currently just chisqr)

        """
        
        # TODO - handle logl properly
        self.setp(pvals)
        # return -self.logl()
        return self.chisqr()

    def logpost(self, pvals=None):
        """
        Calculate the log-probability of the curvefitting system

        Parameters
        ----------
        pvals : array-like or refnx.analysis.Parameters
            values for the varying or entire set of parameters

        Returns
        -------
        logpost : float
            log-probability

        Notes
        -----
        The overall log-probability is the sum of the log-prior and
        log-likelihood. The log-likelihood is not calculated if the log-prior
        is impossible (`logp == -np.inf`).

        """
        self.setp(pvals)
        logpost = self.logp()

        # only calculate the probability if the parameters have finite
        # log-prior
        if not np.isfinite(logpost):
            return -np.inf

        logpost += self.logl()
        return logpost

    def covar(self):
        """
        Estimates the covariance matrix of the curvefitting system.

        Returns
        -------
        covar : np.ndarray
            Covariance matrix

        """
        _pvals = np.array(self.varying_parameters())

        used_residuals_scaler = False

        def residuals_scaler(vals):
            return np.squeeze(self.residuals(_pvals * vals))

        try:
            # we should be able to calculate a Jacobian for a parameter whose
            # value is zero. However, the scaling approach won't work.
            # This will force Jacobian calculation by unscaled parameters
            if np.any(_pvals == 0):
                raise FloatingPointError()

            with np.errstate(invalid="raise"):
                jac = approx_derivative(residuals_scaler, np.ones_like(_pvals))
            used_residuals_scaler = True
        except FloatingPointError:
            jac = approx_derivative(self.residuals, _pvals)
        finally:
            # using approx_derivative changes the state of the objective
            # parameters have to make sure they're set at the end
            self.setp(_pvals)

        # need to create this because GlobalObjective does not have
        # access to all the datapoints being fitted.
        n_datapoints = np.size(jac, 0)

        # covar = J.T x J

        # from scipy.optimize.minpack.py
        # eliminates singular parameters
        _, s, VT = np.linalg.svd(jac, full_matrices=False)
        threshold = np.finfo(float).eps * max(jac.shape) * s[0]
        s = s[s > threshold]
        VT = VT[: s.size]
        covar = np.dot(VT.T / s ** 2, VT)

        if used_residuals_scaler:
            # unwind the scaling.
            covar = covar * np.atleast_2d(_pvals) * np.atleast_2d(_pvals).T

        pvar = np.diagonal(covar).copy()
        psingular = np.where(pvar == 0)[0]

        if len(psingular) > 0:
            var_params = self.varying_parameters()
            singular_params = [var_params[ps] for ps in psingular]

            raise LinAlgError(
                "The following Parameters have no effect on"
                " Objective.residuals, please consider fixing"
                " them.\n" + repr(singular_params)
            )

        scale = 1.0
        # scale by reduced chi2 if experimental uncertainties weren't used.
        if not (self.weighted):
            scale = self.chisqr() / (
                n_datapoints - len(self.varying_parameters())
            )

        return covar * scale

    def pgen(self, ngen=1000, nburn=0, nthin=1):
        """
        Yield random parameter vectors from the MCMC samples. The objective
        state is not altered.

        Parameters
        ----------
        ngen : int, optional
            the number of samples to yield. The actual number of samples
            yielded is `min(ngen, chain.size)`
        nburn : int, optional
            discard this many steps from the start of the chain
        nthin : int, optional
            only accept every `nthin` samples from the chain

        Yields
        ------
        pvec : np.ndarray
            A randomly chosen parameter vector

        """
        yield from self.parameters.pgen(ngen=ngen, nburn=nburn, nthin=nthin)