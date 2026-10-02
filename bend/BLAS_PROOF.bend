import Base
import ./BLAS_LAWS.bend as Laws
import ./blas_shape.bend as Shape

def false_at_true(value: Bool) -> Type:
  match value:
    case True{}:
      Empty
    case False{}:
      Unit

def false_ne_true(equal: {False{} == True{} : Bool}) -> Empty:
  %equal : false_at_true(_)
  Unit{}

def no_vector(-count: Nat, accepted: {None{} == Some{Shape.VectorDims{count}} : Maybe<&2, Shape.VectorDims>}) -> Empty:
  false_ne_true(Equal.cong(Maybe<&2, Shape.VectorDims>, Bool, m => Maybe.is_some(&2, Shape.VectorDims, m), None{}, Some{Shape.VectorDims{count}}, accepted))

def vector_shape(value: Maybe<&2, Shape.VectorDims>) -> List<Nat>:
  match value:
    case None{}:
      []
    case Some{Shape.VectorDims{count}}:
      [count]

def no_matrix(-rows: Nat, -cols: Nat, accepted: {None{} == Some{Shape.MatrixDims{rows, cols}} : Maybe<&2, Shape.MatrixDims>}) -> Empty:
  false_ne_true(Equal.cong(Maybe<&2, Shape.MatrixDims>, Bool, m => Maybe.is_some(&2, Shape.MatrixDims, m), None{}, Some{Shape.MatrixDims{rows, cols}}, accepted))

def matrix_shape(value: Maybe<&2, Shape.MatrixDims>) -> List<Nat>:
  match value:
    case None{}:
      []
    case Some{Shape.MatrixDims{rows, cols}}:
      [rows, cols]

def same_nat(+count: Nat) -> {Nat.is_eq(count, count) == True{} : Bool}:
  match count:
    case 0n:
      {==}
    case 1n+pred:
      same_nat(pred)

def and_components(a: Bool, b: Bool, c: Bool, accepted: {a && (b && c) == True{} : Bool}) -> {(a, (b, c)) == (True{}, (True{}, True{})) : Pair(Bool, Pair(Bool, Bool))}:
  match a b c:
    case False{} b c:
      Empty.absurd({(False{}, (b, c)) == (True{}, (True{}, True{})) : Pair(Bool, Pair(Bool, Bool))}, false_ne_true(accepted))
    case True{} False{} c:
      Empty.absurd({(True{}, (False{}, c)) == (True{}, (True{}, True{})) : Pair(Bool, Pair(Bool, Bool))}, false_ne_true(accepted))
    case True{} True{} False{}:
      Empty.absurd({(True{}, (True{}, False{})) == (True{}, (True{}, True{})) : Pair(Bool, Pair(Bool, Bool))}, false_ne_true(accepted))
    case True{} True{} True{}:
      {==}

def Laws.vector_reconstructs_shape(shape, count, accepted):
  match shape:
    case []:
      Empty.absurd({[] == [count] : List<Nat>}, no_vector(count, accepted))
    case [n]:
      Equal.cong(Maybe<&2, Shape.VectorDims>, List<Nat>, m => vector_shape(m), Some{Shape.VectorDims{n}}, Some{Shape.VectorDims{count}}, accepted)
    case a <> b <> tail:
      Empty.absurd({a <> b <> tail == [count] : List<Nat>}, no_vector(count, accepted))

def Laws.vector_rank_accepted(count):
  {==}

def Laws.matrix_reconstructs_shape(shape, rows, cols, accepted):
  match shape:
    case []:
      Empty.absurd({[] == [rows, cols] : List<Nat>}, no_matrix(rows, cols, accepted))
    case [a]:
      Empty.absurd({[a] == [rows, cols] : List<Nat>}, no_matrix(rows, cols, accepted))
    case [a, b]:
      Equal.cong(Maybe<&2, Shape.MatrixDims>, List<Nat>, m => matrix_shape(m), Some{Shape.MatrixDims{a, b}}, Some{Shape.MatrixDims{rows, cols}}, accepted)
    case a <> b <> c <> tail:
      Empty.absurd({a <> b <> c <> tail == [rows, cols] : List<Nat>}, no_matrix(rows, cols, accepted))

def Laws.matrix_rank_accepted(rows, cols):
  {==}

def Laws.vectors_same_count_accepted(count):
  same_nat(count)

def Laws.vectors_accepted_counts_equal(left, right, accepted):
  accepted

def Laws.matmul_matching_dimensions_accepted(rows, inner, cols):
  %Equal.sym(Bool, Nat.is_eq(inner, inner), True{}, same_nat(inner)) : {_ && (Nat.is_eq(rows, rows) && Nat.is_eq(cols, cols)) == True{} : Bool}
  %Equal.sym(Bool, Nat.is_eq(rows, rows), True{}, same_nat(rows)) : {True{} && (_ && Nat.is_eq(cols, cols)) == True{} : Bool}
  same_nat(cols)

def Laws.matmul_accepted_dimensions_equal(ar, ac, br, bc, out_rows, out_cols, accepted):
  and_components(Nat.is_eq(ac, br), Nat.is_eq(ar, out_rows), Nat.is_eq(bc, out_cols), accepted)
