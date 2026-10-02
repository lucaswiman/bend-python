import Base
import ./python.bend as Python

# NumPy ndarrays and other native float32 buffer exporters. Borrowing never
# normalizes strides, casts values or allocates element storage.
def borrow_f32(value: Python.Object, writable: Bool) -> IO(Python.F32View):
  Python.borrow_f32(value, writable)

def require(allowed: Bool, message: String) -> IO(Unit):
  match allowed:
    case True{}:
      IO.pure(Unit, Unit{})
    case False{}:
      Python.type_error(Unit, message)

def identical(left: Python.Object, right: Python.Object) -> IO(Bool):
  do IO<Bool>:
    operator : Python.Object <- Python.import_module("operator")
    same : Python.Object <- Python.getattr(operator, "is_")
    result : Python.Object <- Python.invoke(same, [left, right])
    Python.to_bool(result)

# Tensor subclasses can override numpy() to return a copy. Restrict this
# adapter to ordinary tensors, whose default conversion guarantees sharing.
def require_tensor(value: Python.Object, torch: Python.Object) -> IO(Unit):
  do IO<Unit>:
    kind : Python.Object <- Python.construct("type", [value])
    tensor : Python.Object <- Python.getattr(torch, "Tensor")
    ordinary : Bool <- identical(kind, tensor)
    require(ordinary, "expected an ordinary torch.Tensor; subclasses are unsupported")

# Forward AD tangents must never be discarded by numpy().
def check_forward_ad(value: Python.Object, dual: Python.Object) -> IO(Unit):
  do IO<Unit>:
    unpack : Python.Object <- Python.getattr(dual, "unpack_dual")
    parts : Python.Object <- Python.invoke(unpack, [value])
    tangent : Python.Object <- Python.getattr(parts, "tangent")
    absent : Python.Object <- Python.none()
    plain : Bool <- identical(tangent, absent)
    require(plain, "forward AD tensors cannot be borrowed")

# Notify PyTorch before granting write access, so partial failures also
# invalidate tensors saved for backward. Read-only borrows do not bump versions.
def notify_write(value: Python.Object, autograd: Python.Object, writable: Bool, view: Python.F32View) -> IO(Python.F32View):
  match writable:
    case False{}:
      IO.pure(Python.F32View, view)
    case True{}:
      do IO<Python.F32View>:
        graph : Python.Object <- Python.getattr(autograd, "graph")
        increment : Python.Object <- Python.getattr(graph, "increment_version")
        ignored : Python.Object <- Python.invoke(increment, [value])
        return view

# numpy() without force=True shares CPU storage; never detach, cast or transfer.
def borrow_torch_f32(+value: Python.Object, +writable: Bool) -> IO(Python.F32View):
  do IO<Python.F32View>:
    grad : Python.Object <- Python.getattr(value, "requires_grad")
    tracked : Bool <- Python.to_bool(grad)
    require(Bool.not(tracked), "detach tensors requiring gradients before borrowing")
    +torch : Python.Object <- Python.import_module("torch")
    require_tensor(value, torch)
    +autograd : Python.Object <- Python.getattr(torch, "autograd")
    dual : Python.Object <- Python.getattr(autograd, "forward_ad")
    check_forward_ad(value, dual)
    numpy : Python.Object <- Python.getattr(value, "numpy")
    array : Python.Object <- Python.invoke(numpy, [])
    view : Python.F32View <- Python.borrow_f32(array, writable)
    notify_write(value, autograd, writable, view)

# The driver executes these memory effects in its native window, without
# calling Python or attaching between elements. No array-sized temporary exists.
def map_loop(~f: F32 -> F32, remaining: Nat, view: Python.F32View, +index: Nat) -> IO(Python.F32View):
  match remaining:
    case 0n:
      IO.pure(Python.F32View, view)
    case 1n+pred:
      do IO<Python.F32View>:
        updated : Python.F32View <- Python.f32_modify(view, index, f)
        map_loop(~f, pred, updated, Nat.add(index, 1n))

def map_start(~f: F32 -> F32, result: Pair(Python.F32View, Nat)) -> IO(Python.F32View):
  (view, count) = result
  map_loop(~f, count, view, 0n)

def map_inplace(~f: F32 -> F32, view: Python.F32View) -> IO(Python.F32View):
  do IO<Python.F32View>:
    result : Pair(Python.F32View, Nat) <- Python.f32_size(view)
    map_start(~f, result)

def map_object(~borrow: Python.Object -> IO(Python.F32View), ~f: F32 -> F32, +value: Python.Object) -> IO(Python.Object):
  do IO<Python.Object>:
    view : Python.F32View <- borrow(value)
    updated : Python.F32View <- map_inplace(~f, view)
    Python.f32_release(updated)
    return value

def invoke_map(~borrow: Python.Object -> IO(Python.F32View), ~f: F32 -> F32, request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)
    map_object(~borrow, ~f, value)

def export_numpy_f32_inplace(~f: F32 -> F32, name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_map(~(value => borrow_f32(value, True{})), ~f), release_gil)

def export_torch_f32_inplace(~f: F32 -> F32, name: String, release_gil: Bool) -> IO(Unit):
  Python.export(name, invoke_map(~(value => borrow_torch_f32(value, True{})), ~f), release_gil)
