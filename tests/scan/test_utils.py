import itertools

import numpy as np
import pytest

import aesara
from aesara import tensor as aet
from aesara.scan.utils import map_variables
from aesara.tensor.type import scalar, vector


class TestMapVariables:
    @staticmethod
    def replacer(graph):
        return getattr(graph.tag, "replacement", graph)

    def test_leaf(self):
        a = scalar("a")
        b = scalar("b")
        c = scalar("c")

        b.tag.replacement = c

        u = a + b
        (v,) = map_variables(self.replacer, [u])

        assert u.owner.inputs == [a, b]
        assert v.owner.inputs == [a, c]

    def test_leaf_inside_scan(self):
        x = vector("x")
        y = scalar("y")
        z = scalar("z")

        y.tag.replacement = z

        s, _ = aesara.scan(lambda x: x * y, sequences=x)
        (s2,) = map_variables(self.replacer, [s])

        f = aesara.function([x, y, z], [s, s2])
        rval = f(x=np.array([1, 2, 3], dtype=np.float32), y=1, z=2)
        assert np.array_equal(rval, [[1, 2, 3], [2, 4, 6]])

    def test_scan(self):
        x = vector("x")

        # we will insert a subgraph involving these variables into the inner
        # graph of scan. since they were not previously in the inner graph,
        # they are like non_sequences to scan(). scan() infers these and
        # imports them into the inner graph properly, and map_variables()
        # should do this as well.
        outer = scalar("outer")
        shared = aesara.shared(np.array(1.0, dtype=aesara.config.floatX), name="shared")
        constant = aet.constant(1, name="constant")

        # z will equal 1 so multiplying by it doesn't change any values
        z = outer * (shared + constant)

        def step(x, a):
            r = a + x
            r.tag.replacement = z * (a - x)
            return r

        s, _ = aesara.scan(step, sequences=x, outputs_info=[np.array(0.0)])
        # ensure z is owned by the outer graph so map_variables() will need to
        # jump through additional hoops to placate FunctionGraph.
        t = z * s
        (s2,) = map_variables(self.replacer, [t])
        t2 = z * s2

        f = aesara.function([x, outer], [t, t2])
        rval = f(x=np.array([1, 2, 3], dtype=np.float32), outer=0.5)
        assert np.array_equal(rval, [[1, 3, 6], [-1, -3, -6]])

    def test_scan_with_shared_update(self):
        x = vector("x")

        # counts how many times its value is used
        counter = aesara.shared(0, name="shared")
        counter.update = counter + 1

        def step(x, a):
            r = a + x
            # introducing a shared variable with an update into the
            # inner graph is unsupported and the code must crash rather
            # than silently produce the wrong result.
            r.tag.replacement = counter * (a - x)
            return r

        s, _ = aesara.scan(step, sequences=x, outputs_info=[np.array(0.0)])
        with pytest.raises(NotImplementedError):
            map_variables(self.replacer, [s])

    def test_scan_with_shared_update2(self):
        x = vector("x")

        # counts how many times its value is used
        counter = aesara.shared(0, name="shared")
        counter.update = counter + 1

        def step(x, a):
            r = a + x
            # introducing a shared variable with an update into the
            # inner graph is unsupported and the code must crash rather
            # than silently produce the wrong result.
            r.tag.replacement = counter * (a - x)
            # the shared variable was already present, but the
            # replacement changes the number of times it is used,
            # which would have to change the updates, which is
            # unsupported.
            return r + counter

        s, _ = aesara.scan(step, sequences=x, outputs_info=[np.array(0.0)])
        with pytest.raises(NotImplementedError):
            map_variables(self.replacer, [s])

    def test_opfromgraph(self):
        # as with the scan tests above, insert foreign inputs into the
        # inner graph.
        outer = scalar("outer")
        shared = aesara.shared(np.array(1.0, dtype=aesara.config.floatX), name="shared")
        constant = aet.constant(1.0, name="constant")
        z = outer * (shared + constant)

        # construct the inner graph
        a = scalar()
        b = scalar()
        r = a + b
        r.tag.replacement = z * (a - b)

        # construct the outer graph
        c = scalar()
        d = scalar()
        u = aesara.compile.builders.OpFromGraph([a, b], [r])(c, d)
        t = z * u
        (v,) = map_variables(self.replacer, [t])
        t2 = z * v

        f = aesara.function([c, d, outer], [t, t2])
        for m, n in itertools.combinations(range(10), 2):
            assert f(m, n, outer=0.5) == [m + n, m - n]

        # test that the unsupported case of replacement with a shared
        # variable with updates crashes
        shared.update = shared + 1
        with pytest.raises(NotImplementedError):
            map_variables(self.replacer, [t])
