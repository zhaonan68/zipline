"""
Tests for Term.
"""
from collections import Counter
from itertools import product
from unittest import TestCase

from zipline.errors import (
    DTypeNotSpecified,
    InputTermNotAtomic,
    InvalidDType,
    TermInputsNotSpecified,
    WindowLengthNotSpecified,
)
from zipline.pipeline import Factor, TermGraph
from zipline.pipeline.data import Column, DataSet
from zipline.pipeline.term import AssetExists, NotSpecified
from zipline.pipeline.expression import NUMEXPR_MATH_FUNCS
from zipline.utils.numpy_utils import (
    datetime64ns_dtype,
    float64_dtype,
)


class SomeDataSet(DataSet):
    foo = Column(float64_dtype)
    bar = Column(float64_dtype)
    buzz = Column(float64_dtype)


class SomeFactor(Factor):
    dtype = float64_dtype
    window_length = 5
    inputs = [SomeDataSet.foo, SomeDataSet.bar]
SomeFactorAlias = SomeFactor


class SomeOtherFactor(Factor):
    dtype = float64_dtype
    window_length = 5
    inputs = [SomeDataSet.bar, SomeDataSet.buzz]


class DateFactor(Factor):
    dtype = datetime64ns_dtype
    window_length = 5
    inputs = [SomeDataSet.bar, SomeDataSet.buzz]


class NoLookbackFactor(Factor):
    dtype = float64_dtype
    window_length = 0


def gen_equivalent_factors():
    """
    Return an iterator of SomeFactor instances that should all be the same
    object.
    """
    yield SomeFactor()
    yield SomeFactor(inputs=NotSpecified)
    yield SomeFactor(SomeFactor.inputs)
    yield SomeFactor(inputs=SomeFactor.inputs)
    yield SomeFactor([SomeDataSet.foo, SomeDataSet.bar])
    yield SomeFactor(window_length=SomeFactor.window_length)
    yield SomeFactor(window_length=NotSpecified)
    yield SomeFactor(
        [SomeDataSet.foo, SomeDataSet.bar],
        window_length=NotSpecified,
    )
    yield SomeFactor(
        [SomeDataSet.foo, SomeDataSet.bar],
        window_length=SomeFactor.window_length,
    )
    yield SomeFactorAlias()


def to_dict(l):
    """
    Convert a list to a dict with keys drawn from '0', '1', '2', ...

    Example
    -------
    >>> to_dict([2, 3, 4])
    {'0': 2, '1': 3, '2': 4}
    """
    return dict(zip(map(str, range(len(l))), l))


class DependencyResolutionTestCase(TestCase):

    def check_dependency_order(self, ordered_terms):
        seen = set()

        for term in ordered_terms:
            for dep in term.dependencies:
                self.assertIn(dep, seen)

            seen.add(term)

    def test_single_factor(self):
        """
        Test dependency resolution for a single factor.
        """
        def check_output(graph):

            resolution_order = list(graph.ordered())

            self.assertEqual(len(resolution_order), 4)
            self.check_dependency_order(resolution_order)
            self.assertIn(AssetExists(), resolution_order)
            self.assertIn(SomeDataSet.foo, resolution_order)
            self.assertIn(SomeDataSet.bar, resolution_order)
            self.assertIn(SomeFactor(), resolution_order)

            self.assertEqual(graph.node[SomeDataSet.foo]['extra_rows'], 4)
            self.assertEqual(graph.node[SomeDataSet.bar]['extra_rows'], 4)

        for foobar in gen_equivalent_factors():
            check_output(TermGraph(to_dict([foobar])))

    def test_single_factor_instance_args(self):
        """
        Test dependency resolution for a single factor with arguments passed to
        the constructor.
        """
        bar, buzz = SomeDataSet.bar, SomeDataSet.buzz
        graph = TermGraph(to_dict([SomeFactor([bar, buzz], window_length=5)]))

        resolution_order = list(graph.ordered())

        # SomeFactor, its inputs, and AssetExists()
        self.assertEqual(len(resolution_order), 4)
        self.check_dependency_order(resolution_order)
        self.assertIn(AssetExists(), resolution_order)
        self.assertEqual(graph.extra_rows[AssetExists()], 4)

        self.assertIn(bar, resolution_order)
        self.assertIn(buzz, resolution_order)
        self.assertIn(SomeFactor([bar, buzz], window_length=5),
                      resolution_order)
        self.assertEqual(graph.extra_rows[bar], 4)
        self.assertEqual(graph.extra_rows[buzz], 4)

    def test_reuse_atomic_terms(self):
        """
        Test that raw inputs only show up in the dependency graph once.
        """
        f1 = SomeFactor([SomeDataSet.foo, SomeDataSet.bar])
        f2 = SomeOtherFactor([SomeDataSet.bar, SomeDataSet.buzz])

        graph = TermGraph(to_dict([f1, f2]))
        resolution_order = list(graph.ordered())

        # bar should only appear once.
        self.assertEqual(len(resolution_order), 6)
        self.assertEqual(len(set(resolution_order)), 6)
        self.check_dependency_order(resolution_order)

    def test_disallow_recursive_lookback(self):

        with self.assertRaises(InputTermNotAtomic):
            SomeFactor(inputs=[SomeFactor(), SomeDataSet.foo])


class ObjectIdentityTestCase(TestCase):

    def assertSameObject(self, *objs):
        first = objs[0]
        for obj in objs:
            self.assertIs(first, obj)

    def assertDifferentObjects(self, *objs):
        id_counts = Counter(map(id, objs))
        ((most_common_id, count),) = id_counts.most_common(1)
        if count > 1:
            dupe = [o for o in objs if id(o) == most_common_id][0]
            self.fail("%s appeared %d times in %s" % (dupe, count, objs))

    def test_instance_caching(self):

        self.assertSameObject(*gen_equivalent_factors())
        self.assertIs(
            SomeFactor(window_length=SomeFactor.window_length + 1),
            SomeFactor(window_length=SomeFactor.window_length + 1),
        )

        self.assertIs(
            SomeFactor(dtype=float64_dtype),
            SomeFactor(dtype=float64_dtype),
        )

        self.assertIs(
            SomeFactor(inputs=[SomeFactor.inputs[1], SomeFactor.inputs[0]]),
            SomeFactor(inputs=[SomeFactor.inputs[1], SomeFactor.inputs[0]]),
        )

    def test_instance_non_caching(self):

        f = SomeFactor()

        # Different window_length.
        self.assertIsNot(
            f,
            SomeFactor(window_length=SomeFactor.window_length + 1),
        )

        # Different dtype
        self.assertIsNot(
            f,
            SomeFactor(dtype=datetime64ns_dtype)
        )

        # Reordering inputs changes semantics.
        self.assertIsNot(
            f,
            SomeFactor(inputs=[SomeFactor.inputs[1], SomeFactor.inputs[0]]),
        )

    def test_instance_non_caching_redefine_class(self):

        orig_foobar_instance = SomeFactorAlias()

        class SomeFactor(Factor):
            dtype = float64_dtype
            window_length = 5
            inputs = [SomeDataSet.foo, SomeDataSet.bar]

        self.assertIsNot(orig_foobar_instance, SomeFactor())

    def test_instance_caching_binops(self):
        f = SomeFactor()
        g = SomeOtherFactor()
        for lhs, rhs in product([f, g], [f, g]):
            self.assertIs((lhs + rhs), (lhs + rhs))
            self.assertIs((lhs - rhs), (lhs - rhs))
            self.assertIs((lhs * rhs), (lhs * rhs))
            self.assertIs((lhs / rhs), (lhs / rhs))
            self.assertIs((lhs ** rhs), (lhs ** rhs))

        self.assertIs((1 + rhs), (1 + rhs))
        self.assertIs((rhs + 1), (rhs + 1))

        self.assertIs((1 - rhs), (1 - rhs))
        self.assertIs((rhs - 1), (rhs - 1))

        self.assertIs((2 * rhs), (2 * rhs))
        self.assertIs((rhs * 2), (rhs * 2))

        self.assertIs((2 / rhs), (2 / rhs))
        self.assertIs((rhs / 2), (rhs / 2))

        self.assertIs((2 ** rhs), (2 ** rhs))
        self.assertIs((rhs ** 2), (rhs ** 2))

        self.assertIs((f + g) + (f + g), (f + g) + (f + g))

    def test_instance_caching_unary_ops(self):
        f = SomeFactor()
        self.assertIs(-f, -f)
        self.assertIs(--f, --f)
        self.assertIs(---f, ---f)

    def test_instance_caching_math_funcs(self):
        f = SomeFactor()
        for funcname in NUMEXPR_MATH_FUNCS:
            method = getattr(f, funcname)
            self.assertIs(method(), method())

    def test_parameterized_term(self):

        class SomeFactorParameterized(SomeFactor):
            params = ('a', 'b')

        f = SomeFactorParameterized(a=1, b=2)
        self.assertEqual(f.params, {'a': 1, 'b': 2})

        g = SomeFactorParameterized(a=1, b=3)
        h = SomeFactorParameterized(a=2, b=2)
        self.assertDifferentObjects(f, g, h)

        f2 = SomeFactorParameterized(a=1, b=2)
        f3 = SomeFactorParameterized(b=2, a=1)
        self.assertSameObject(f, f2, f3)

        self.assertEqual(f.params['a'], 1)
        self.assertEqual(f.params['b'], 2)
        self.assertEqual(f.window_length, SomeFactor.window_length)
        self.assertEqual(f.inputs, tuple(SomeFactor.inputs))

    def test_bad_input(self):

        class SomeFactor(Factor):
            dtype = float64_dtype

        class SomeFactorDefaultInputs(SomeFactor):
            inputs = (SomeDataSet.foo, SomeDataSet.bar)

        class SomeFactorDefaultLength(SomeFactor):
            window_length = 10

        class SomeFactorNoDType(SomeFactor):
            window_length = 10
            inputs = (SomeDataSet.foo,)
            dtype = NotSpecified

        with self.assertRaises(TermInputsNotSpecified):
            SomeFactor(window_length=1)

        with self.assertRaises(TermInputsNotSpecified):
            SomeFactorDefaultLength()

        with self.assertRaises(WindowLengthNotSpecified):
            SomeFactor(inputs=(SomeDataSet.foo,))

        with self.assertRaises(WindowLengthNotSpecified):
            SomeFactorDefaultInputs()

        with self.assertRaises(DTypeNotSpecified):
            SomeFactorNoDType()

        with self.assertRaises(InvalidDType):
            SomeFactor(dtype=1)
