import Base

# These copyable records describe logical dimensions, never native storage.
type VectorDims is Data:
  VectorDims{count: Nat}

type MatrixDims is Data:
  MatrixDims{rows: Nat, cols: Nat}

# f32_shape returns an affine List<Nat>. Consume its metadata once; the
# resulting dimensions are Data, so callers can reuse only those tiny records.
def vector(shape: List<Nat>) -> Maybe<&2, VectorDims>:
  match shape:
    case [count]:
      Some{VectorDims{count}}
    case _:
      None{}

def matrix(shape: List<Nat>) -> Maybe<&2, MatrixDims>:
  match shape:
    case [rows, cols]:
      Some{MatrixDims{rows, cols}}
    case _:
      None{}

def compatible_vectors(left: Nat, right: Nat) -> Bool:
  Nat.is_eq(left, right)

def compatible_matmul(ar: Nat, ac: Nat, br: Nat, bc: Nat, out_rows: Nat, out_cols: Nat) -> Bool:
  Nat.is_eq(ac, br) && (Nat.is_eq(ar, out_rows) && Nat.is_eq(bc, out_cols))
