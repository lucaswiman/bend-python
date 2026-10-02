import Base
import ./blas_shape.bend as Shape

# Pure logical contracts only. No claim about buffer storage or BLAS arithmetic.
law vector_reconstructs_shape:
  for shape: List<Nat>
  for count: Nat
  for accepted: {Shape.vector(shape) == Some{Shape.VectorDims{count}} : Maybe<&2, Shape.VectorDims>}
  {shape == [count] : List<Nat>}

law vector_rank_accepted:
  for count: Nat
  {Shape.vector([count]) == Some{Shape.VectorDims{count}} : Maybe<&2, Shape.VectorDims>}

law matrix_reconstructs_shape:
  for shape: List<Nat>
  for rows: Nat
  for cols: Nat
  for accepted: {Shape.matrix(shape) == Some{Shape.MatrixDims{rows, cols}} : Maybe<&2, Shape.MatrixDims>}
  {shape == [rows, cols] : List<Nat>}

law matrix_rank_accepted:
  for rows: Nat
  for cols: Nat
  {Shape.matrix([rows, cols]) == Some{Shape.MatrixDims{rows, cols}} : Maybe<&2, Shape.MatrixDims>}

law vectors_same_count_accepted:
  for +count: Nat
  {Shape.compatible_vectors(count, count) == True{} : Bool}

law vectors_accepted_counts_equal:
  for +left: Nat
  for +right: Nat
  for accepted: {Shape.compatible_vectors(left, right) == True{} : Bool}
  {left == right : Nat}

law matmul_matching_dimensions_accepted:
  for +rows: Nat
  for +inner: Nat
  for +cols: Nat
  {Shape.compatible_matmul(rows, inner, inner, cols, rows, cols) == True{} : Bool}

law matmul_accepted_inner_equal:
  for +ar: Nat
  for +ac: Nat
  for +br: Nat
  for +bc: Nat
  for +out_rows: Nat
  for +out_cols: Nat
  for accepted: {Shape.compatible_matmul(ar, ac, br, bc, out_rows, out_cols) == True{} : Bool}
  {ac == br : Nat}

law matmul_accepted_rows_equal:
  for +ar: Nat
  for +ac: Nat
  for +br: Nat
  for +bc: Nat
  for +out_rows: Nat
  for +out_cols: Nat
  for accepted: {Shape.compatible_matmul(ar, ac, br, bc, out_rows, out_cols) == True{} : Bool}
  {ar == out_rows : Nat}

law matmul_accepted_cols_equal:
  for +ar: Nat
  for +ac: Nat
  for +br: Nat
  for +bc: Nat
  for +out_rows: Nat
  for +out_cols: Nat
  for accepted: {Shape.compatible_matmul(ar, ac, br, bc, out_rows, out_cols) == True{} : Bool}
  {bc == out_cols : Nat}
