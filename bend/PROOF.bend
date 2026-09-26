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

def zero_discriminator(value: Nat) -> Type:
  match value:
    case 0n:
      Unit
    case 1n+_:
      Empty

def zero_not_succ(-n: Nat, equal: {0n == 1n+n : Nat}) -> Empty:
  %equal : zero_discriminator(_)
  Unit{}

def succ_discriminator(value: Nat) -> Type:
  match value:
    case 0n:
      Empty
    case 1n+_:
      Unit

def succ_not_zero(-n: Nat, equal: {1n+n == 0n : Nat}) -> Empty:
  %equal : succ_discriminator(_)
  Unit{}

def predecessor(value: Nat) -> Nat:
  match value:
    case 0n:
      0n
    case 1n+pred:
      pred

def succ_injective(-a: Nat, -b: Nat, equal: {1n+a == 1n+b : Nat}) -> {a == b : Nat}:
  %equal : {a == predecessor(_) : Nat}
  {==}

def Laws.singleton_complete(xs, exact):
  match xs:
    case []:
      Empty.absurd({Python.singleton([]) == Python.argument([], 0n) : Maybe<Python.Object>}, zero_not_succ(0n, exact))
    case [_]:
      {==}
    case head <> _ <> tail:
      Empty.absurd({None{} == Some{head} : Maybe<Python.Object>}, succ_not_zero(Python.arity(tail), succ_injective(1n+Python.arity(tail), 0n, exact)))

def Laws.pair_complete(xs, exact):
  match xs:
    case []:
      Empty.absurd({None{} == None{} : Maybe<Pair(Python.Object, Python.Object)>}, zero_not_succ(1n, exact))
    case [_]:
      Empty.absurd({None{} == None{} : Maybe<Pair(Python.Object, Python.Object)>}, zero_not_succ(0n, succ_injective(0n, 1n, exact)))
    case [_, _]:
      {==}
    case left <> right <> _ <> tail:
      Empty.absurd({None{} == Some{(left, right)} : Maybe<Pair(Python.Object, Python.Object)>}, succ_not_zero(Python.arity(tail), succ_injective(1n+Python.arity(tail), 0n, succ_injective(1n+1n+Python.arity(tail), 1n, exact))))
