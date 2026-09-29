import Base

def square(+x: U32) -> U32:
  (x * x : U32)

def increment(x: U32) -> U32:
  (x + 1 : U32)

def identity(x: U32) -> U32:
  x

def add(x: U32, y: U32) -> U32:
  (x + y : U32)

def flip(x: Bool) -> Bool:
  Bool.not(x)

def half(x: F32) -> F32:
  (x / 2.0 : F32)

def rounds(n: Nat, acc: U32) -> U32:
  match n:
    case 0n:
      acc
    case 1n+pred:
      rounds(pred, ((acc * 1664525 : U32) + 1013904223 : U32))

# A finite pure workload for demonstrating optional GIL release. Every iteration
# advances a modular U32 LCG by 32 rounds; recursion is structural in n.
def countdown(n: Nat, acc: U32) -> U32:
  match n:
    case 0n:
      acc
    case 1n+pred:
      countdown(pred, rounds(32n, acc))

def slow(n: U32) -> U32:
  countdown(U32.to_nat(n), 0)

def sum(values: List<U32>, acc: U32) -> U32:
  match values:
    case []:
      acc
    case head <> tail:
      sum(tail, (acc + head : U32))
