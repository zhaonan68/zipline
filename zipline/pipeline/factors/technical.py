"""
Technical Analysis Factors
--------------------------
"""
from bottleneck import (
    nanargmax,
    nanmax,
    nanmean,
    nansum,
)
from numbers import Number
from numpy import (
    abs,
    arange,
    average,
    clip,
    diff,
    exp,
    fmax,
    full,
    inf,
    isnan,
    log,
    NINF,
    sqrt,
    sum as np_sum,
)
from numexpr import evaluate

from zipline.pipeline.data import USEquityPricing
from zipline.pipeline.mixins import SingleInputMixin
from zipline.utils.control_flow import ignore_nanwarnings
from zipline.utils.input_validation import expect_types
from .factor import CustomFactor


class Returns(CustomFactor):
    """
    Calculates the percent change in close price over the given window_length.

    **Default Inputs**: [USEquityPricing.close]
    """
    inputs = [USEquityPricing.close]

    def compute(self, today, assets, out, close):
        out[:] = (close[-1] - close[0]) / close[0]


class RSI(CustomFactor, SingleInputMixin):
    """
    Relative Strength Index

    **Default Inputs**: [USEquityPricing.close]

    **Default Window Length**: 15
    """
    window_length = 15
    inputs = (USEquityPricing.close,)

    def compute(self, today, assets, out, closes):
        diffs = diff(closes, axis=0)
        ups = nanmean(clip(diffs, 0, inf), axis=0)
        downs = abs(nanmean(clip(diffs, -inf, 0), axis=0))
        return evaluate(
            "100 - (100 / (1 + (ups / downs)))",
            local_dict={'ups': ups, 'downs': downs},
            global_dict={},
            out=out,
        )


class SimpleMovingAverage(CustomFactor, SingleInputMixin):
    """
    Average Value of an arbitrary column

    **Default Inputs**: None

    **Default Window Length**: None
    """
    # numpy's nan functions throw warnings when passed an array containing only
    # nans, but they still returns the desired value (nan), so we ignore the
    # warning.
    ctx = ignore_nanwarnings()

    def compute(self, today, assets, out, data):
        out[:] = nanmean(data, axis=0)


class WeightedAverageValue(CustomFactor):
    """
    Helper for VWAP-like computations.

    **Default Inputs:** None

    **Default Window Length:** None
    """
    def compute(self, today, assets, out, base, weight):
        out[:] = nansum(base * weight, axis=0) / nansum(weight, axis=0)


class VWAP(WeightedAverageValue):
    """
    Volume Weighted Average Price

    **Default Inputs:** [USEquityPricing.close, USEquityPricing.volume]

    **Default Window Length:** None
    """
    inputs = (USEquityPricing.close, USEquityPricing.volume)


class MaxDrawdown(CustomFactor, SingleInputMixin):
    """
    Max Drawdown

    **Default Inputs:** None

    **Default Window Length:** None
    """
    ctx = ignore_nanwarnings()

    def compute(self, today, assets, out, data):
        drawdowns = fmax.accumulate(data, axis=0) - data
        drawdowns[isnan(drawdowns)] = NINF
        drawdown_ends = nanargmax(drawdowns, axis=0)

        # TODO: Accelerate this loop in Cython or Numba.
        for i, end in enumerate(drawdown_ends):
            peak = nanmax(data[:end + 1, i])
            out[i] = (peak - data[end, i]) / data[end, i]


def DollarVolume():
    """
    Returns a Factor computing the product of most recent close price and
    volume.
    """
    return USEquityPricing.close.latest * USEquityPricing.volume.latest


class _ExponentialWeightedFactor(SingleInputMixin, CustomFactor):
    """
    Base class for factors implementing exponential-weighted operations.

    **Default Inputs:** None

    **Default Window Length:** None

    Parameters
    ----------
    inputs : length-1 list or tuple of BoundColumn
        The expression over which to compute the average.
    window_length : int > 0
        Length of the lookback window over which to compute the average.
    decay_rate : float, 0 < decay_rate <= 1
        Weighting factor by which to discount past observations.

        When calculating historical averages, rows are multiplied by the
        sequence::

            decay_rate, decay_rate ** 2, decay_rate ** 3, ...

    Methods
    -------
    weights
    from_span
    from_halflife
    from_center_of_mass
    """
    params = ('decay_rate',)

    @staticmethod
    def weights(length, decay_rate):
        """
        Return weighting vector for an exponential moving statistic on `length`
        rows with a decay rate of `decay_rate`.
        """
        return full(length, decay_rate) ** arange(length + 1, 1, -1)

    @classmethod
    @expect_types(span=Number)
    def from_span(cls, inputs, window_length, span):
        """
        Convenience constructor for passing `decay_rate` in terms of `span`.

        Forwards `decay_rate` as `1 - (2.0 / (1 + span))`.  This provides the
        behavior equivalent to passing `span` to pandas.ewma.

        Example
        -------
        .. code-block:: python

            # Equivalent to:
            # my_ewma = EWMA(
            #    inputs=[USEquityPricing.close],
            #    window_length=30,
            #    decay_rate=(1 - (2.0 / (1 + 15.0))),
            # )
            my_ewma = EWMA.from_span(
                inputs=[USEquityPricing.close],
                window_length=30,
                span=15,
            )

        Note
        ----
        This classmethod is provided by both
        :class:`ExponentialWeightedMovingAverage` and
        :class:`ExponentialWeightedMovingStdDev`.
        """
        if span <= 1:
            raise ValueError(
                "`span` must be a positive number. %s was passed." % span
            )

        decay_rate = (1.0 - (2.0 / (1.0 + span)))
        assert 0.0 < decay_rate <= 1.0

        return cls(
            inputs=inputs,
            window_length=window_length,
            decay_rate=decay_rate,
        )

    @classmethod
    @expect_types(halflife=Number)
    def from_halflife(cls, inputs, window_length, halflife):
        """
        Convenience constructor for passing ``decay_rate`` in terms of half
        life.

        Forwards ``decay_rate`` as ``exp(log(.5) / halflife)``.  This provides
        the behavior equivalent to passing `halflife` to pandas.ewma.

        Example
        -------
        .. code-block:: python

            # Equivalent to:
            # my_ewma = EWMA(
            #    inputs=[USEquityPricing.close],
            #    window_length=30,
            #    decay_rate=np.exp(np.log(0.5) / 15),
            # )
            my_ewma = EWMA.from_halflife(
                inputs=[USEquityPricing.close],
                window_length=30,
                halflife=15,
            )

        Note
        ----
        This classmethod is provided by both
        :class:`ExponentialWeightedMovingAverage` and
        :class:`ExponentialWeightedMovingStdDev`.
        """
        if halflife <= 0:
            raise ValueError(
                "`span` must be a positive number. %s was passed." % halflife
            )
        decay_rate = exp(log(.5) / halflife)
        assert 0.0 < decay_rate <= 1.0

        return cls(
            inputs=inputs,
            window_length=window_length,
            decay_rate=decay_rate,
        )

    @classmethod
    def from_center_of_mass(cls, inputs, window_length, center_of_mass):
        """
        Convenience constructor for passing `decay_rate` in terms of center of
        mass.

        Forwards `decay_rate` as `1 - (1 / 1 + center_of_mass)`.  This provides
        behavior equivalent to passing `center_of_mass` to pandas.ewma.

        Example
        -------
        .. code-block:: python

            # Equivalent to:
            # my_ewma = EWMA(
            #    inputs=[USEquityPricing.close],
            #    window_length=30,
            #    decay_rate=(1 - (1 / 15.0)),
            # )
            my_ewma = EWMA.from_center_of_mass(
                inputs=[USEquityPricing.close],
                window_length=30,
                center_of_mass=15,
            )

        Note
        ----
        This classmethod is provided by both
        :class:`ExponentialWeightedMovingAverage` and
        :class:`ExponentialWeightedMovingStdDev`.
        """
        return cls(
            inputs=inputs,
            window_length=window_length,
            decay_rate=(1.0 - (1.0 / (1.0 + center_of_mass))),
        )


class ExponentialWeightedMovingAverage(_ExponentialWeightedFactor):
    """
    Exponentially Weighted Moving Average

    **Default Inputs:** None

    **Default Window Length:** None

    Parameters
    ----------
    inputs : length-1 list/tuple of BoundColumn
        The expression over which to compute the average.
    window_length : int > 0
        Length of the lookback window over which to compute the average.
    decay_rate : float, 0 < decay_rate <= 1
        Weighting factor by which to discount past observations.

        When calculating historical averages, rows are multiplied by the
        sequence::

            decay_rate, decay_rate ** 2, decay_rate ** 3, ...

    Notes
    -----
    - This class can also be imported under the name ``EWMA``.

    See Also
    --------
    :func:`pandas.ewma`
    """
    def compute(self, today, assets, out, data, decay_rate):
        out[:] = average(
            data,
            axis=0,
            weights=self.weights(len(data), decay_rate),
        )


class ExponentialWeightedMovingStdDev(_ExponentialWeightedFactor):
    """
    Exponentially Weighted Moving Standard Deviation

    **Default Inputs:** None

    **Default Window Length:** None

    Parameters
    ----------
    inputs : length-1 list/tuple of BoundColumn
        The expression over which to compute the average.
    window_length : int > 0
        Length of the lookback window over which to compute the average.
    decay_rate : float, 0 < decay_rate <= 1
        Weighting factor by which to discount past observations.

        When calculating historical averages, rows are multiplied by the
        sequence::

            decay_rate, decay_rate ** 2, decay_rate ** 3, ...

    Notes
    -----
    - This class can also be imported under the name ``EWMSTD``.

    See Also
    --------
    :func:`pandas.ewmstd`
    """

    def compute(self, today, assets, out, data, decay_rate):
        weights = self.weights(len(data), decay_rate)

        mean = average(data, axis=0, weights=weights)
        variance = average((data - mean) ** 2, axis=0, weights=weights)

        squared_weight_sum = (np_sum(weights) ** 2)
        bias_correction = (
            squared_weight_sum / (squared_weight_sum - np_sum(weights ** 2))
        )
        out[:] = sqrt(variance * bias_correction)


# Convenience aliases.
EWMA = ExponentialWeightedMovingAverage
EWMSTD = ExponentialWeightedMovingStdDev
