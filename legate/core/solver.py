# Copyright 2021 NVIDIA Corporation
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
#

from .utils import compute_volume


class Expression(object):
    def __repr__(self):
        return str(self)


class Offset(Expression):
    def __init__(self, expr, offset):
        self.expr = expr
        self.offset = offset

    @property
    def closed(self):
        return self.expr.closed

    def __str__(self):
        return f"{self.expr} + {self.offset}"

    def collapse(self):
        expr = self.expr.collapse()
        if isinstance(expr, Tile):
            return Tile(expr.tile_size, self.offset)
        else:
            return type(self)(expr, self.offset)

    def substitute(self, subst):
        return Offset(self.expr.substitute(subst), self.offset)

    def invert(self, rhs):
        assert not self.expr.closed
        return self.expr, Offset(rhs, -self.offset)

    def find_term(self):
        return self.expr.find_term()


class Tile(Expression):
    def __init__(self, tile_size, offset=0):
        self.tile_size = tile_size
        self.offset = offset

    @property
    def closed(self):
        return True

    def __str__(self):
        return f"tile({self.tile_size}, {self.offset})"

    def collapse(self):
        return self

    def substitute(self, subst):
        return self

    def invert(self, rhs):
        raise RuntimeError("Invalid inversion")

    def find_term(self):
        raise RuntimeError("Invalid call")


class Dimension(Expression):
    def __init__(self, index, shape):
        self.index = index
        self.shape = tuple(dim for dim in shape)

    @property
    def closed(self):
        return False

    def __eq__(self, other):
        if not isinstance(other, Expression):
            raise ValueError(f"Unknown expression type: {type(other)}")
        return Match(self, other)

    def __le__(self, other):
        if not isinstance(other, Expression):
            raise ValueError(f"Unknown expression type: {type(other)}")
        return Subsume(self, other)

    def __add__(self, other):
        if not isinstance(other, int):
            raise ValueError(f"Unknown offset type: {type(other)}")
        return Offset(self, other)

    def __str__(self):
        return f"{self.shape.name}_{self.index}"

    def collapse(self):
        raise RuntimeError("Dimension variable cannot be collapsed")

    def substitute(self, subst):
        return subst[self] if self in subst else self

    def invert(self, rhs):
        return self, rhs

    def find_term(self):
        return self

    def __hash__(self):
        return hash(repr(self))


class Constraint(object):
    def __init__(self, lhs, rhs, op):
        self.lhs = lhs
        self.rhs = rhs
        self.op = op

    def __str__(self):
        return f"{self.lhs} {self.op} {self.rhs}"

    def __repr__(self):
        return str(self)

    @property
    def closed(self):
        return self.lhs.closed or self.rhs.closed

    def substitute(self, subst):
        lhs = self.lhs.substitute(subst)
        rhs = self.rhs.substitute(subst)
        return type(self)(lhs, rhs)


class Match(Constraint):
    def __init__(self, lhs, rhs):
        super(Match, self).__init__(lhs, rhs, "==")


class Subsume(Constraint):
    def __init__(self, lhs, rhs):
        super(Subsume, self).__init__(lhs, rhs, ">=")


def _cast_tuple(value, ndim):
    if isinstance(value, Shape):
        return value._shape
    elif isinstance(value, tuple):
        return value
    elif isinstance(value, int):
        return (value,) * ndim
    else:
        raise ValueError(f"Cannot cast {type(value).__name__} to tuple")


class Shape(object):
    def __init__(self, shape):
        self._shape = tuple(shape)
        self._volume = compute_volume(shape)

    def __str__(self):
        return str(self._shape)

    def __getitem__(self, idx):
        return self._shape[idx]

    def __len__(self):
        return len(self._shape)

    @property
    def ndim(self):
        return len(self._shape)

    @property
    def volume(self):
        return self._volume

    def __hash__(self):
        return hash((self.__class__, self._shape))

    def __le__(self, other):
        lh = _cast_tuple(self, self.ndim)
        rh = _cast_tuple(other, self.ndim)
        return len(lh) == len(rh) and lh <= rh

    def __eq__(self, other):
        lh = _cast_tuple(self, self.ndim)
        rh = _cast_tuple(other, self.ndim)
        return lh == rh

    def __add__(self, other):
        lh = _cast_tuple(self, self.ndim)
        rh = _cast_tuple(other, self.ndim)
        return Shape(tuple(a + b for (a, b) in zip(lh, rh)))

    def __sub__(self, other):
        lh = _cast_tuple(self, self.ndim)
        rh = _cast_tuple(other, self.ndim)
        return Shape(tuple(a - b for (a, b) in zip(lh, rh)))

    def __mul__(self, other):
        lh = _cast_tuple(self, self.ndim)
        rh = _cast_tuple(other, self.ndim)
        return Shape(tuple(a * b for (a, b) in zip(lh, rh)))

    def __mod__(self, other):
        lh = _cast_tuple(self, self.ndim)
        rh = _cast_tuple(other, self.ndim)
        return Shape(tuple(a % b for (a, b) in zip(lh, rh)))

    def __floordiv__(self, other):
        lh = _cast_tuple(self, self.ndim)
        rh = _cast_tuple(other, self.ndim)
        return Shape(tuple(a // b for (a, b) in zip(lh, rh)))

    def drop(self, dim):
        return Shape(self._shape[:dim] + self._shape[dim + 1 :])

    def update(self, dim, new_value):
        return Shape(self._shape[:dim] + (new_value,) + self._shape[dim + 1 :])

    def insert(self, dim, new_value):
        return Shape(self._shape[:dim] + (new_value,) + self._shape[dim:])
