import Base
import ./python.bend as Python

# Pure storage/lease specification, not a proof of C loads, stores or lifetime.
# Elements are F32 terms (words), so equality includes NaN and signed-zero bits.
type Lease is Data:
  Live{writable: Bool}
  Released{}

def writable(lease: Lease) -> Bool:
  match lease:
    case Live{allowed}:
      allowed
    case Released{}:
      False{}

def close(lease: Lease) -> Maybe<&2, Lease>:
  match lease:
    case Live{_}:
      Some{Released{}}
    case Released{}:
      None{}

def read(memory: List<&2, F32>, index: Nat) -> Maybe<&2, F32>:
  List.get(&2, F32, memory, index)

def replace(memory: List<&2, F32>, index: Nat, value: F32) -> List<&2, F32>:
  List.set(&2, F32, memory, index, value)

def accept(allowed: Bool, memory: List<&2, F32>, index: Nat, value: F32) -> Maybe<List<&2, F32>>:
  match allowed:
    case True{}:
      Some{replace(memory, index, value)}
    case False{}:
      None{}

def write(+lease: Lease, +memory: List<&2, F32>, +index: Nat, value: F32) -> Maybe<List<&2, F32>>:
  accept(writable(lease) && Nat.is_lt(index, List.length(&2, F32, memory)), memory, index, value)

# Pure cursor arithmetic model; no proof of C/CPython correspondence.
def position(length: Nat, remaining: Nat) -> Nat:
  Nat.sub(length, remaining)

def remaining_after_step(remaining: Nat) -> Nat:
  Nat.sub(remaining, 1n)

# Probe each affine step once; no assumptions about its scalar result.
def step_count(count: Nat, steps: Python.F32MapSteps(count)) -> Nat:
  match count:
    case 0n:
      0n
    case 1n+pred:
      1n+step_count(pred, Pair.snd(F32, Python.F32MapSteps(pred), steps(0.0)))

def cons_step(-count: Nat, result: Pair(F32, Python.F32MapSteps(count)), tail: Python.F32MapSteps(count) -> List<F32>) -> List<F32>:
  (value, next) = result
  value <> tail(next)

# Pure bounded-prefix interpreter; production supplies the actual element count.
def apply_steps(count: Nat, memory: List<F32>, steps: Python.F32MapSteps(count)) -> List<F32>:
  match count memory:
    case 0n _:
      memory
    case 1n+pred Nil{}:
      []
    case 1n+pred head <> tail:
      cons_step(pred, steps(head), next => apply_steps(pred, tail, next))

# One axis of a mixed-radix cursor. Carry resets the coordinate and restores
# its remaining extent; native signed byte strides are outside this model.
def advance_axis(coordinate: Nat, remaining: Nat) -> Pair(Bool, Pair(Nat, Nat)):
  match remaining:
    case 0n:
      (True{}, (0n, coordinate))
    case 1n+pred:
      (False{}, (1n+coordinate, pred))

# Closed-function semantics without per-cell successor closures or tuples.
def apply_closed(~f: F32 -> F32, memory: List<F32>) -> List<F32>:
  match memory:
    case []:
      []
    case head <> tail:
      f(head) <> apply_closed(~f, tail)

def axis_extent_after(result: Pair(Bool, Pair(Nat, Nat))) -> Nat:
  (carry, axis) = result
  (coordinate, remaining) = axis
  Nat.add(coordinate, remaining)
