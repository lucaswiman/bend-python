import Base
import ./python.bend as Python

law arity_empty:
  {Python.arity([]) == 0n : Nat}

law arity_cons:
  for x: Python.Object
  for xs: List<Python.Object>
  {Python.arity(x <> xs) == 1n+Python.arity(xs) : Nat}

law argument_head:
  for +x: Python.Object
  for xs: List<Python.Object>
  {Python.argument(x <> xs, 0n) == Some{x} : Maybe<Python.Object>}

law argument_tail:
  for x: Python.Object
  for xs: List<Python.Object>
  for +index: Nat
  {Python.argument(x <> xs, 1n+index) == Python.argument(xs, index) : Maybe<Python.Object>}

law argument_empty:
  for index: Nat
  {Python.argument([], index) == None{} : Maybe<Python.Object>}

law singleton_roundtrip:
  for +x: Python.Object
  {Python.singleton([x]) == Some{x} : Maybe<Python.Object>}

law pair_roundtrip:
  for +x: Python.Object
  for +y: Python.Object
  {Python.pair([x, y]) == Some{(x, y)} : Maybe<Pair(Python.Object, Python.Object)>}

law argument_at_end:
  for xs: List<Python.Object>
  {Python.argument(xs, Python.arity(xs)) == None{} : Maybe<Python.Object>}

law singleton_exact_arity:
  for xs: List<Python.Object>
  for value: Python.Object
  for success: {Python.singleton(xs) == Some{value} : Maybe<Python.Object>}
  {Python.arity(xs) == 1n : Nat}

law pair_exact_arity:
  for xs: List<Python.Object>
  for left: Python.Object
  for right: Python.Object
  for success: {Python.pair(xs) == Some{(left, right)} : Maybe<Pair(Python.Object, Python.Object)>}
  {Python.arity(xs) == 2n : Nat}

# Converses of the exact-arity laws: every list of the right length parses.
# Together these characterize the pure parsers; the IO wrappers are not covered.
law singleton_complete:
  for xs: List<Python.Object>
  for exact: {Python.arity(xs) == 1n : Nat}
  {Python.singleton(xs) == Python.argument(xs, 0n) : Maybe<Python.Object>}

law pair_complete:
  for xs: List<Python.Object>
  for exact: {Python.arity(xs) == 2n : Nat}
  {Python.pair(xs) == Maybe.bind(&1, Python.Object, Pair(Python.Object, Python.Object), Python.argument(xs, 0n), left => Maybe.bind(&1, Python.Object, Pair(Python.Object, Python.Object), Python.argument(xs, 1n), right => Some{(left, right)})) : Maybe<Pair(Python.Object, Python.Object)>}
