import Base

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
