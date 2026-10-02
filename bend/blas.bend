import Base
import ./python.bend as Python
import ./tensor.bend as Tensor
import ./blas_shape.bend as Shape

# Metadata is consumed once; only the small, copyable dimension records survive.
def require_vector(-writable: Bool, view: Python.F32View<writable>, dims: Maybe<&2, Shape.VectorDims>) -> IO(Pair(Python.F32View<writable>, Shape.VectorDims)):
  match dims:
    case Some{shape}:
      IO.pure(Pair(Python.F32View<writable>, Shape.VectorDims), (view, shape))
    case None{}:
      do IO<Pair(Python.F32View<writable>, Shape.VectorDims)>:
        Python.f32_release(writable, view)
        Python.type_error(Pair(Python.F32View<writable>, Shape.VectorDims), "BLAS vector arguments must have rank one")

def vector_shape(-writable: Bool, values: Pair(Python.F32View<writable>, List<Nat>)) -> IO(Pair(Python.F32View<writable>, Shape.VectorDims)):
  (view, shape) = values
  require_vector(writable, view, Shape.vector(shape))

def vector(-writable: Bool, view: Python.F32View<writable>) -> IO(Pair(Python.F32View<writable>, Shape.VectorDims)):
  do IO<Pair(Python.F32View<writable>, Shape.VectorDims)>:
    values : Pair(Python.F32View<writable>, List<Nat>) <- Python.f32_shape(writable, view)
    vector_shape(writable, values)

def require_matrix(-writable: Bool, view: Python.F32View<writable>, dims: Maybe<&2, Shape.MatrixDims>) -> IO(Pair(Python.F32View<writable>, Shape.MatrixDims)):
  match dims:
    case Some{shape}:
      IO.pure(Pair(Python.F32View<writable>, Shape.MatrixDims), (view, shape))
    case None{}:
      do IO<Pair(Python.F32View<writable>, Shape.MatrixDims)>:
        Python.f32_release(writable, view)
        Python.type_error(Pair(Python.F32View<writable>, Shape.MatrixDims), "BLAS matrix arguments must have rank two")

def matrix_shape(-writable: Bool, values: Pair(Python.F32View<writable>, List<Nat>)) -> IO(Pair(Python.F32View<writable>, Shape.MatrixDims)):
  (view, shape) = values
  require_matrix(writable, view, Shape.matrix(shape))

def matrix(-writable: Bool, view: Python.F32View<writable>) -> IO(Pair(Python.F32View<writable>, Shape.MatrixDims)):
  do IO<Pair(Python.F32View<writable>, Shape.MatrixDims)>:
    values : Pair(Python.F32View<writable>, List<Nat>) <- Python.f32_shape(writable, view)
    matrix_shape(writable, values)

def scale_vector(alpha: F32, values: Pair(Python.F32View<True{}>, Shape.VectorDims)) -> IO(Unit):
  (view, _) = values
  do IO<Unit>:
    updated : Python.F32View<True{}> <- Python.blas_scale(view, alpha)
    Python.f32_release(True{}, updated)

def scale_object(~write: Python.Object -> IO(Python.F32View<True{}>), alpha_object: Python.Object, +target: Python.Object) -> IO(Python.Object):
  do IO<Python.Object>:
    alpha : F32 <- Python.to_f32(alpha_object)
    view : Python.F32View<True{}> <- write(target)
    shaped : Pair(Python.F32View<True{}>, Shape.VectorDims) <- vector(True{}, view)
    scale_vector(alpha, shaped)
    return target

def scale_values(~write: Python.Object -> IO(Python.F32View<True{}>), values: Pair(Python.Object, Python.Object)) -> IO(Python.Object):
  (alpha, target) = values
  scale_object(~write, alpha, target)

def invoke_scale(~write: Python.Object -> IO(Python.F32View<True{}>), request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    values : Pair(Python.Object, Python.Object) <- Python.binary(request)
    scale_values(~write, values)

def close_dot(values: Pair(Python.F32View<False{}>, Pair(Python.F32View<False{}>, F32))) -> IO(Python.Object):
  (left, (right, result)) = values
  do IO<Python.Object>:
    Python.f32_release(False{}, left)
    Python.f32_release(False{}, right)
    Python.from_f32(result)

def dot_vectors(left_values: Pair(Python.F32View<False{}>, Shape.VectorDims), right_values: Pair(Python.F32View<False{}>, Shape.VectorDims)) -> IO(Python.Object):
  (left, Shape.VectorDims{left_count}) = left_values
  (right, Shape.VectorDims{right_count}) = right_values
  do IO<Python.Object>:
    Tensor.require(Shape.compatible_vectors(left_count, right_count), "BLAS vectors must have equal lengths")
    result : Pair(Python.F32View<False{}>, Pair(Python.F32View<False{}>, F32)) <- Python.blas_dot(False{}, False{}, left, right)
    close_dot(result)

def dot_objects(~read: Python.Object -> IO(Python.F32View<False{}>), left: Python.Object, right: Python.Object) -> IO(Python.Object):
  do IO<Python.Object>:
    left_view : Python.F32View<False{}> <- read(left)
    left_shape : Pair(Python.F32View<False{}>, Shape.VectorDims) <- vector(False{}, left_view)
    right_view : Python.F32View<False{}> <- read(right)
    right_shape : Pair(Python.F32View<False{}>, Shape.VectorDims) <- vector(False{}, right_view)
    dot_vectors(left_shape, right_shape)

def dot_values(~read: Python.Object -> IO(Python.F32View<False{}>), values: Pair(Python.Object, Python.Object)) -> IO(Python.Object):
  (left, right) = values
  dot_objects(~read, left, right)

def invoke_dot(~read: Python.Object -> IO(Python.F32View<False{}>), request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    values : Pair(Python.Object, Python.Object) <- Python.binary(request)
    dot_values(~read, values)

def close_axpy(values: Pair(Python.F32View<False{}>, Python.F32View<True{}>)) -> IO(Unit):
  (source, target) = values
  do IO<Unit>:
    Python.f32_release(False{}, source)
    Python.f32_release(True{}, target)

def axpy_vectors(alpha: F32, source_values: Pair(Python.F32View<False{}>, Shape.VectorDims), target_values: Pair(Python.F32View<True{}>, Shape.VectorDims)) -> IO(Unit):
  (source, Shape.VectorDims{source_count}) = source_values
  (target, Shape.VectorDims{target_count}) = target_values
  do IO<Unit>:
    Tensor.require(Shape.compatible_vectors(source_count, target_count), "BLAS vectors must have equal lengths")
    result : Pair(Python.F32View<False{}>, Python.F32View<True{}>) <- Python.blas_axpy(False{}, source, target, alpha)
    close_axpy(result)

def axpy_objects(~read: Python.Object -> IO(Python.F32View<False{}>), ~write: Python.Object -> IO(Python.F32View<True{}>), alpha_object: Python.Object, source: Python.Object, +target: Python.Object) -> IO(Python.Object):
  do IO<Python.Object>:
    alpha : F32 <- Python.to_f32(alpha_object)
    source_view : Python.F32View<False{}> <- read(source)
    source_shape : Pair(Python.F32View<False{}>, Shape.VectorDims) <- vector(False{}, source_view)
    target_view : Python.F32View<True{}> <- write(target)
    target_shape : Pair(Python.F32View<True{}>, Shape.VectorDims) <- vector(True{}, target_view)
    axpy_vectors(alpha, source_shape, target_shape)
    return target

# Three-argument extraction stays local; the core SDK needs no extra arity API.
def invoke_axpy(~read: Python.Object -> IO(Python.F32View<False{}>), ~write: Python.Object -> IO(Python.F32View<True{}>), request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{[alpha, source, target], keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        axpy_objects(~read, ~write, alpha, source, target)
    case _:
      Python.type_error(Python.Object, "expected exactly three positional arguments")

def close_matmul(values: Pair(Python.F32View<False{}>, Pair(Python.F32View<False{}>, Python.F32View<True{}>))) -> IO(Unit):
  (left, (right, out)) = values
  do IO<Unit>:
    Python.f32_release(False{}, left)
    Python.f32_release(False{}, right)
    Python.f32_release(True{}, out)

def matmul_matrices(left_values: Pair(Python.F32View<False{}>, Shape.MatrixDims), right_values: Pair(Python.F32View<False{}>, Shape.MatrixDims), out_values: Pair(Python.F32View<True{}>, Shape.MatrixDims)) -> IO(Unit):
  (left, Shape.MatrixDims{ar, ac}) = left_values
  (right, Shape.MatrixDims{br, bc}) = right_values
  (out, Shape.MatrixDims{out_rows, out_cols}) = out_values
  do IO<Unit>:
    Tensor.require(Shape.compatible_matmul(ar, ac, br, bc, out_rows, out_cols), "BLAS matrix dimensions are incompatible")
    result : Pair(Python.F32View<False{}>, Pair(Python.F32View<False{}>, Python.F32View<True{}>)) <- Python.blas_matmul(False{}, False{}, left, right, out)
    close_matmul(result)

def matmul_objects(~read: Python.Object -> IO(Python.F32View<False{}>), ~write: Python.Object -> IO(Python.F32View<True{}>), left: Python.Object, right: Python.Object, +out: Python.Object) -> IO(Python.Object):
  do IO<Python.Object>:
    left_view : Python.F32View<False{}> <- read(left)
    left_shape : Pair(Python.F32View<False{}>, Shape.MatrixDims) <- matrix(False{}, left_view)
    right_view : Python.F32View<False{}> <- read(right)
    right_shape : Pair(Python.F32View<False{}>, Shape.MatrixDims) <- matrix(False{}, right_view)
    out_view : Python.F32View<True{}> <- write(out)
    out_shape : Pair(Python.F32View<True{}>, Shape.MatrixDims) <- matrix(True{}, out_view)
    matmul_matrices(left_shape, right_shape, out_shape)
    return out

def invoke_matmul(~read: Python.Object -> IO(Python.F32View<False{}>), ~write: Python.Object -> IO(Python.F32View<True{}>), request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{[left, right, out], keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        matmul_objects(~read, ~write, left, right, out)
    case _:
      Python.type_error(Python.Object, "expected exactly three positional arguments")

# Template specialization produces captureless callbacks for native exports.
def export_scale(name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_scale(~(value => Tensor.borrow_f32(value, True{}))), release_gil)

def export_dot(name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_dot(~(value => Tensor.borrow_f32(value, False{}))), release_gil)

def export_axpy(name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_axpy(~(value => Tensor.borrow_f32(value, False{})), ~(value => Tensor.borrow_f32(value, True{}))), release_gil)

def export_matmul(name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_matmul(~(value => Tensor.borrow_f32(value, False{})), ~(value => Tensor.borrow_f32(value, True{}))), release_gil)

def export_torch_scale(name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_scale(~(value => Tensor.borrow_torch_f32(value, True{}))), release_gil)

def export_torch_dot(name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_dot(~(value => Tensor.borrow_torch_f32(value, False{}))), release_gil)

def export_torch_axpy(name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_axpy(~(value => Tensor.borrow_torch_f32(value, False{})), ~(value => Tensor.borrow_torch_f32(value, True{}))), release_gil)

def export_torch_matmul(name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_matmul(~(value => Tensor.borrow_torch_f32(value, False{})), ~(value => Tensor.borrow_torch_f32(value, True{}))), release_gil)
