import Base
import ./arithmetic.bend as Arithmetic

law identity:
  for +x: U32
  {Arithmetic.identity(x) == x : U32}

law square:
  for +x: U32
  {Arithmetic.square(x) == (x * x : U32) : U32}

law increment:
  for +x: U32
  {Arithmetic.increment(x) == (x + 1 : U32) : U32}

law add:
  for +x: U32
  for +y: U32
  {Arithmetic.add(x, y) == (x + y : U32) : U32}

law flip_involution:
  for +x: Bool
  {Arithmetic.flip(Arithmetic.flip(x)) == x : Bool}

law countdown_zero:
  for +acc: U32
  {Arithmetic.countdown(0n, acc) == acc : U32}

law countdown_step:
  for +n: Nat
  for +acc: U32
  {Arithmetic.countdown(1n+n, acc) == Arithmetic.countdown(n, Arithmetic.rounds(32n, acc)) : U32}

law rounds_zero:
  for +acc: U32
  {Arithmetic.rounds(0n, acc) == acc : U32}

law rounds_step:
  for +n: Nat
  for +acc: U32
  {Arithmetic.rounds(1n+n, acc) == Arithmetic.rounds(n, ((acc * 1664525 : U32) + 1013904223 : U32)) : U32}

# Unlike the definitional laws above, these need induction: running a+b rounds
# is running a rounds and then b more, so slow work splits into sequential parts.
law rounds_compose:
  for a: Nat
  for +b: Nat
  for acc: U32
  {Arithmetic.rounds((a + b : Nat), acc) == Arithmetic.rounds(b, Arithmetic.rounds(a, acc)) : U32}

law countdown_compose:
  for a: Nat
  for +b: Nat
  for acc: U32
  {Arithmetic.countdown((a + b : Nat), acc) == Arithmetic.countdown(b, Arithmetic.countdown(a, acc)) : U32}
