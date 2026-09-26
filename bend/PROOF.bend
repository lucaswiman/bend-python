import Base
import ./LAWS.bend as Laws
import ./python.bend as Python

def Laws.arity_empty():
  {==}

def Laws.arity_cons(x, xs):
  {==}

def Laws.argument_head(x, xs):
  {==}

def Laws.argument_tail(x, xs, index):
  {==}

def Laws.argument_empty(index):
  {==}

def Laws.singleton_roundtrip(x):
  {==}

def Laws.pair_roundtrip(x, y):
  {==}

def Laws.argument_at_end(xs):
  match xs:
    case []:
      {==}
    case _ <> tail:
      Laws.argument_at_end(tail)

def none_discriminator(-A: Type, value: Maybe<A>) -> Type:
  match value:
    case None{}:
      Unit
    case Some{_}:
      Empty

def none_not_some(-A: Type, -value: A, success: {None{} == Some{value} : Maybe<A>}) -> Empty:
  %success : none_discriminator(A, _)
  Unit{}

def Laws.singleton_exact_arity(xs, value, success):
  match xs:
    case []:
      Empty.absurd({0n == 1n : Nat}, none_not_some(Python.Object, value, success))
    case [_]:
      {==}
    case _ <> _ <> tail:
      Empty.absurd({1n+1n+Python.arity(tail) == 1n : Nat}, none_not_some(Python.Object, value, success))

def Laws.pair_exact_arity(xs, left, right, success):
  match xs:
    case []:
      Empty.absurd({0n == 2n : Nat}, none_not_some(Pair(Python.Object, Python.Object), (left, right), success))
    case [_]:
      Empty.absurd({1n == 2n : Nat}, none_not_some(Pair(Python.Object, Python.Object), (left, right), success))
    case [_, _]:
      {==}
    case _ <> _ <> _ <> tail:
      Empty.absurd({1n+1n+1n+Python.arity(tail) == 2n : Nat}, none_not_some(Pair(Python.Object, Python.Object), (left, right), success))
