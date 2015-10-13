#
# Copyright 2015 Quantopian, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Tests for USEquityPricingLoader and related classes.
"""
from unittest import TestCase

from numpy import (
    arange,
    datetime64,
    float64,
    ones,
    uint32,
)
from numpy.testing import (
    assert_allclose,
    assert_array_equal,
)
from pandas import (
    concat,
    DataFrame,
    Int64Index,
    Timestamp,
)
from testfixtures import TempDirectory

from zipline.lib.adjustment import Float64Multiply
from zipline.pipeline.loaders.synthetic import (
    NullAdjustmentReader,
    SyntheticDailyBarWriter,
)
from zipline.data.us_equity_pricing import (
    BcolzDailyBarReader,
    SQLiteAdjustmentReader,
    SQLiteAdjustmentWriter,
)
from zipline.pipeline.loaders.equity_pricing_loader import (
    USEquityPricingLoader,
)

from zipline.errors import WindowLengthTooLong
from zipline.finance.trading import TradingEnvironment
from zipline.pipeline.data import USEquityPricing
from zipline.utils.test_utils import (
    seconds_to_timestamp,
    str_to_seconds,
)

# Test calendar ranges over the month of June 2015
#      June 2015
# Mo Tu We Th Fr Sa Su
#  1  2  3  4  5  6  7
#  8  9 10 11 12 13 14
# 15 16 17 18 19 20 21
# 22 23 24 25 26 27 28
# 29 30
TEST_CALENDAR_START = Timestamp('2015-06-01', tz='UTC')
TEST_CALENDAR_STOP = Timestamp('2015-06-30', tz='UTC')

TEST_QUERY_START = Timestamp('2015-06-10', tz='UTC')
TEST_QUERY_STOP = Timestamp('2015-06-19', tz='UTC')

# One asset for each of the cases enumerated in load_raw_arrays_from_bcolz.
EQUITY_INFO = DataFrame(
    [
        # 1) The equity's trades start and end before query.
        {'start_date': '2015-06-01', 'end_date': '2015-06-05'},
        # 2) The equity's trades start and end after query.
        {'start_date': '2015-06-22', 'end_date': '2015-06-30'},
        # 3) The equity's data covers all dates in range.
        {'start_date': '2015-06-02', 'end_date': '2015-06-30'},
        # 4) The equity's trades start before the query start, but stop
        #    before the query end.
        {'start_date': '2015-06-01', 'end_date': '2015-06-15'},
        # 5) The equity's trades start and end during the query.
        {'start_date': '2015-06-12', 'end_date': '2015-06-18'},
        # 6) The equity's trades start during the query, but extend through
        #    the whole query.
        {'start_date': '2015-06-15', 'end_date': '2015-06-25'},
    ],
    index=arange(1, 7),
    columns=['start_date', 'end_date'],
).astype(datetime64)

TEST_QUERY_ASSETS = EQUITY_INFO.index


# ADJUSTMENTS use the following scheme to indicate information about the value
# upon inspection.
#
# 1s place is the equity
#
# 0.1s place is the action type, with:
#
# splits, 1
# mergers, 2
# dividends, 3
#
# 0.001s is the date
SPLITS = DataFrame(
    [
        # Before query range, should be excluded.
        {'effective_date': str_to_seconds('2015-06-03'),
         'ratio': 1.103,
         'sid': 1},
        # First day of query range, should be excluded.
        {'effective_date': str_to_seconds('2015-06-10'),
         'ratio': 3.110,
         'sid': 3},
        # Third day of query range, should have last_row of 2
        {'effective_date': str_to_seconds('2015-06-12'),
         'ratio': 3.112,
         'sid': 3},
        # After query range, should be excluded.
        {'effective_date': str_to_seconds('2015-06-21'),
         'ratio': 6.121,
         'sid': 6},
        # Another action in query range, should have last_row of 1
        {'effective_date': str_to_seconds('2015-06-11'),
         'ratio': 3.111,
         'sid': 3},
        # Last day of range.  Should have last_row of 7
        {'effective_date': str_to_seconds('2015-06-19'),
         'ratio': 3.119,
         'sid': 3},
    ],
    columns=['effective_date', 'ratio', 'sid'],
)


MERGERS = DataFrame(
    [
        # Before query range, should be excluded.
        {'effective_date': str_to_seconds('2015-06-03'),
         'ratio': 1.203,
         'sid': 1},
        # First day of query range, should be excluded.
        {'effective_date': str_to_seconds('2015-06-10'),
         'ratio': 3.210,
         'sid': 3},
        # Third day of query range, should have last_row of 2
        {'effective_date': str_to_seconds('2015-06-12'),
         'ratio': 3.212,
         'sid': 3},
        # After query range, should be excluded.
        {'effective_date': str_to_seconds('2015-06-25'),
         'ratio': 6.225,
         'sid': 6},
        # Another action in query range, should have last_row of 2
        {'effective_date': str_to_seconds('2015-06-12'),
         'ratio': 4.212,
         'sid': 4},
        # Last day of range.  Should have last_row of 7
        {'effective_date': str_to_seconds('2015-06-19'),
         'ratio': 3.219,
         'sid': 3},
    ],
    columns=['effective_date', 'ratio', 'sid'],
)


DIVIDENDS = DataFrame(
    [
        # Before query range, should be excluded.
        {'effective_date': str_to_seconds('2015-06-01'),
         'ratio': 1.301,
         'sid': 1},
        # First day of query range, should be excluded.
        {'effective_date': str_to_seconds('2015-06-10'),
         'ratio': 3.310,
         'sid': 3},
        # Third day of query range, should have last_row of 2
        {'effective_date': str_to_seconds('2015-06-12'),
         'ratio': 3.312,
         'sid': 3},
        # After query range, should be excluded.
        {'effective_date': str_to_seconds('2015-06-25'),
         'ratio': 6.325,
         'sid': 6},
        # Another action in query range, should have last_row of 3
        {'effective_date': str_to_seconds('2015-06-15'),
         'ratio': 3.315,
         'sid': 3},
        # Last day of range.  Should have last_row of 7
        {'effective_date': str_to_seconds('2015-06-19'),
         'ratio': 3.319,
         'sid': 3},
    ],
    columns=['effective_date', 'ratio', 'sid'],
)


class USEquityPricingLoaderTestCase(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.test_data_dir = TempDirectory()
        cls.db_path = cls.test_data_dir.getpath('adjustments.db')
        writer = SQLiteAdjustmentWriter(cls.db_path)
        writer.write(SPLITS, MERGERS, DIVIDENDS)

        cls.assets = TEST_QUERY_ASSETS
        all_days = TradingEnvironment().trading_days
        cls.calendar_days = all_days[
            all_days.slice_indexer(TEST_CALENDAR_START, TEST_CALENDAR_STOP)
        ]

        cls.asset_info = EQUITY_INFO
        cls.bcolz_writer = SyntheticDailyBarWriter(
            cls.asset_info,
            cls.calendar_days,
        )
        cls.bcolz_path = cls.test_data_dir.getpath('equity_pricing.bcolz')
        cls.bcolz_writer.write(cls.bcolz_path, cls.calendar_days, cls.assets)

    @classmethod
    def tearDownClass(cls):
        cls.test_data_dir.cleanup()

    def test_input_sanity(self):
        # Ensure that the input data doesn't contain adjustments during periods
        # where the corresponding asset didn't exist.
        for table in SPLITS, MERGERS, DIVIDENDS:
            for eff_date_secs, _, sid in table.itertuples(index=False):
                eff_date = Timestamp(eff_date_secs, unit='s')
                asset_start, asset_end = EQUITY_INFO.ix[
                    sid, ['start_date', 'end_date']
                ]
                self.assertGreaterEqual(eff_date, asset_start)
                self.assertLessEqual(eff_date, asset_end)

    def calendar_days_between(self, start_date, end_date, shift=0):
        slice_ = self.calendar_days.slice_indexer(start_date, end_date)
        start = slice_.start + shift
        stop = slice_.stop + shift
        if start < 0:
            raise KeyError(start_date, shift)

        return self.calendar_days[start:stop]

    def expected_adjustments(self, start_date, end_date):
        price_adjustments = {}
        volume_adjustments = {}
        query_days = self.calendar_days_between(start_date, end_date)
        start_loc = query_days.get_loc(start_date)

        for table in SPLITS, MERGERS, DIVIDENDS:
            for eff_date_secs, ratio, sid in table.itertuples(index=False):
                eff_date = Timestamp(eff_date_secs, unit='s', tz='UTC')

                # Ignore adjustments outside the query bounds.
                if not (start_date <= eff_date <= end_date):
                    continue

                eff_date_loc = query_days.get_loc(eff_date)
                delta = eff_date_loc - start_loc

                # Pricing adjustments should be applied on the date
                # corresponding to the effective date of the input data. They
                # should affect all rows **before** the effective date.
                price_adjustments.setdefault(delta, []).append(
                    Float64Multiply(
                        first_row=0,
                        last_row=delta,
                        first_col=sid - 1,
                        last_col=sid - 1,
                        value=ratio,
                    )
                )
                # Volume is *inversely* affected by *splits only*.
                if table is SPLITS:
                    volume_adjustments.setdefault(delta, []).append(
                        Float64Multiply(
                            first_row=0,
                            last_row=delta,
                            first_col=sid - 1,
                            last_col=sid - 1,
                            value=1.0 / ratio,
                        )
                    )
        return price_adjustments, volume_adjustments

    def test_load_adjustments_from_sqlite(self):
        reader = SQLiteAdjustmentReader(self.db_path)
        columns = [USEquityPricing.close, USEquityPricing.volume]
        query_days = self.calendar_days_between(
            TEST_QUERY_START,
            TEST_QUERY_STOP,
        )

        adjustments = reader.load_adjustments(
            columns,
            query_days,
            self.assets,
        )

        close_adjustments = adjustments[0]
        volume_adjustments = adjustments[1]

        expected_close_adjustments, expected_volume_adjustments = \
            self.expected_adjustments(TEST_QUERY_START, TEST_QUERY_STOP)
        self.assertEqual(close_adjustments, expected_close_adjustments)
        self.assertEqual(volume_adjustments, expected_volume_adjustments)

    def test_read_no_adjustments(self):
        adjustment_reader = NullAdjustmentReader()
        columns = [USEquityPricing.close, USEquityPricing.volume]
        query_days = self.calendar_days_between(
            TEST_QUERY_START,
            TEST_QUERY_STOP
        )
        # Our expected results for each day are based on values from the
        # previous day.
        shifted_query_days = self.calendar_days_between(
            TEST_QUERY_START,
            TEST_QUERY_STOP,
            shift=-1,
        )

        adjustments = adjustment_reader.load_adjustments(
            columns,
            query_days,
            self.assets,
        )
        self.assertEqual(adjustments, [{}, {}])

        baseline_reader = BcolzDailyBarReader(self.bcolz_path)
        pricing_loader = USEquityPricingLoader(
            baseline_reader,
            adjustment_reader,
        )

        closes, volumes = pricing_loader.load_adjusted_array(
            columns,
            dates=query_days,
            assets=self.assets,
            mask=ones((len(query_days), len(self.assets)), dtype=bool),
        )

        expected_baseline_closes = self.bcolz_writer.expected_values_2d(
            shifted_query_days,
            self.assets,
            'close',
        )
        expected_baseline_volumes = self.bcolz_writer.expected_values_2d(
            shifted_query_days,
            self.assets,
            'volume',
        )

        # AdjustedArrays should yield the same data as the expected baseline.
        for windowlen in range(1, len(query_days) + 1):
            for offset, window in enumerate(closes.traverse(windowlen)):
                assert_array_equal(
                    expected_baseline_closes[offset:offset + windowlen],
                    window,
                )

            for offset, window in enumerate(volumes.traverse(windowlen)):
                assert_array_equal(
                    expected_baseline_volumes[offset:offset + windowlen],
                    window,
                )

        # Verify that we checked up to the longest possible window.
        with self.assertRaises(WindowLengthTooLong):
            closes.traverse(windowlen + 1)
        with self.assertRaises(WindowLengthTooLong):
            volumes.traverse(windowlen + 1)

    def apply_adjustments(self, dates, assets, baseline_values, adjustments):
        min_date, max_date = dates[[0, -1]]
        # HACK: Simulate the coercion to float64 we do in adjusted_array.  This
        # should be removed when AdjustedArray properly supports
        # non-floating-point types.
        orig_dtype = baseline_values.dtype
        values = baseline_values.astype(float64).copy()
        for eff_date_secs, ratio, sid in adjustments.itertuples(index=False):
            eff_date = seconds_to_timestamp(eff_date_secs)
            # Don't apply adjustments that aren't in the current date range.
            if eff_date not in dates:
                continue
            eff_date_loc = dates.get_loc(eff_date)
            asset_col = assets.get_loc(sid)
            # Apply ratio multiplicatively to the asset column on all rows less
            # than or equal adjustment effective date.
            values[:eff_date_loc + 1, asset_col] *= ratio
        return values.astype(orig_dtype)

    def test_read_with_adjustments(self):
        columns = [USEquityPricing.high, USEquityPricing.volume]
        query_days = self.calendar_days_between(
            TEST_QUERY_START,
            TEST_QUERY_STOP
        )
        # Our expected results for each day are based on values from the
        # previous day.
        shifted_query_days = self.calendar_days_between(
            TEST_QUERY_START,
            TEST_QUERY_STOP,
            shift=-1,
        )

        baseline_reader = BcolzDailyBarReader(self.bcolz_path)
        adjustment_reader = SQLiteAdjustmentReader(self.db_path)
        pricing_loader = USEquityPricingLoader(
            baseline_reader,
            adjustment_reader,
        )

        highs, volumes = pricing_loader.load_adjusted_array(
            columns,
            dates=query_days,
            assets=Int64Index(arange(1, 7)),
            mask=ones((len(query_days), 6), dtype=bool),
        )

        expected_baseline_highs = self.bcolz_writer.expected_values_2d(
            shifted_query_days,
            self.assets,
            'high',
        )
        expected_baseline_volumes = self.bcolz_writer.expected_values_2d(
            shifted_query_days,
            self.assets,
            'volume',
        )

        # At each point in time, the AdjustedArrays should yield the baseline
        # with all adjustments up to that date applied.
        for windowlen in range(1, len(query_days) + 1):
            for offset, window in enumerate(highs.traverse(windowlen)):
                baseline = expected_baseline_highs[offset:offset + windowlen]
                baseline_dates = query_days[offset:offset + windowlen]
                expected_adjusted_highs = self.apply_adjustments(
                    baseline_dates,
                    self.assets,
                    baseline,
                    # Apply all adjustments.
                    concat([SPLITS, MERGERS, DIVIDENDS], ignore_index=True),
                )
                assert_allclose(expected_adjusted_highs, window)

            for offset, window in enumerate(volumes.traverse(windowlen)):
                baseline = expected_baseline_volumes[offset:offset + windowlen]
                baseline_dates = query_days[offset:offset + windowlen]
                # Apply only splits and invert the ratio.
                adjustments = SPLITS.copy()
                adjustments.ratio = 1 / adjustments.ratio

                expected_adjusted_volumes = self.apply_adjustments(
                    baseline_dates,
                    self.assets,
                    baseline,
                    adjustments,
                )
                # FIXME: Make AdjustedArray properly support integral types.
                assert_array_equal(
                    expected_adjusted_volumes,
                    window.astype(uint32),
                )

        # Verify that we checked up to the longest possible window.
        with self.assertRaises(WindowLengthTooLong):
            highs.traverse(windowlen + 1)
        with self.assertRaises(WindowLengthTooLong):
            volumes.traverse(windowlen + 1)
